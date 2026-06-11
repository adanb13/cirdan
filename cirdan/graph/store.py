"""SQLite-backed graph store. One file holds the graph, incidents, actions, and events.

Merging policy on upsert: evidence is unioned, attrs are shallow-merged with the
newer write winning, confidence keeps the strongest label, origin static+live
becomes "both", and last_seen is touched.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import networkx as nx

from cirdan.graph.schema import (
    Confidence,
    Edge,
    Node,
    Origin,
    Relation,
    merge_confidence,
    merge_origin,
)
from cirdan.util import now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    source_adapter TEXT,
    confidence TEXT,
    evidence TEXT,
    attrs TEXT,
    first_seen TEXT,
    last_seen TEXT,
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence TEXT,
    evidence TEXT,
    attrs TEXT,
    first_seen TEXT,
    last_seen TEXT,
    PRIMARY KEY (source, target, relation)
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    status TEXT,
    severity TEXT,
    started_at TEXT,
    updated_at TEXT,
    data TEXT
);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    status TEXT,
    created_at TEXT,
    data TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    source_type TEXT,
    provider TEXT,
    severity TEXT,
    resource TEXT,
    service TEXT,
    message TEXT,
    error_family TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_service ON events (service);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _node_from_row(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        type=row["type"],
        name=row["name"],
        origin=Origin(row["origin"]),
        source_adapter=row["source_adapter"] or "",
        confidence=Confidence(row["confidence"] or "UNKNOWN"),
        evidence=json.loads(row["evidence"] or "[]"),
        attrs=json.loads(row["attrs"] or "{}"),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        deleted=bool(row["deleted"]),
    )


def _edge_from_row(row: sqlite3.Row) -> Edge:
    return Edge(
        source=row["source"],
        target=row["target"],
        relation=Relation(row["relation"]),
        confidence=Confidence(row["confidence"] or "UNKNOWN"),
        evidence=json.loads(row["evidence"] or "[]"),
        attrs=json.loads(row["attrs"] or "{}"),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
    )


class GraphStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- nodes -------------------------------------------------------------

    def upsert_node(self, node: Node) -> Node:
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE id=?", (node.id,)).fetchone()
            if row:
                existing = _node_from_row(row)
                merged = Node(
                    id=node.id,
                    type=node.type or existing.type,
                    name=node.name or existing.name,
                    origin=merge_origin(existing.origin, node.origin),
                    source_adapter=node.source_adapter or existing.source_adapter,
                    confidence=merge_confidence(existing.confidence, node.confidence),
                    evidence=list(dict.fromkeys(existing.evidence + node.evidence)),
                    attrs={**existing.attrs, **node.attrs},
                    first_seen=existing.first_seen,
                    last_seen=now_iso(),
                    deleted=False,
                )
            else:
                merged = node.model_copy(update={"last_seen": now_iso()})
            self._conn.execute(
                """INSERT OR REPLACE INTO nodes
                   (id, type, name, origin, source_adapter, confidence, evidence, attrs,
                    first_seen, last_seen, deleted)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    merged.id,
                    merged.type,
                    merged.name,
                    merged.origin.value,
                    merged.source_adapter,
                    merged.confidence.value,
                    json.dumps(merged.evidence),
                    json.dumps(merged.attrs, default=str),
                    merged.first_seen,
                    merged.last_seen,
                    int(merged.deleted),
                ),
            )
            self._conn.commit()
            return merged

    def get_node(self, node_id: str) -> Node | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return _node_from_row(row) if row else None

    def all_nodes(self, include_deleted: bool = False) -> list[Node]:
        sql = "SELECT * FROM nodes" + ("" if include_deleted else " WHERE deleted=0")
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [_node_from_row(r) for r in rows]

    def find_nodes(
        self,
        type: str | None = None,
        name: str | None = None,
        name_contains: str | None = None,
        origin: Origin | None = None,
    ) -> list[Node]:
        clauses, params = ["deleted=0"], []
        if type:
            clauses.append("type=?")
            params.append(type)
        if name:
            clauses.append("LOWER(name)=LOWER(?)")
            params.append(name)
        if name_contains:
            clauses.append("LOWER(name) LIKE LOWER(?)")
            params.append(f"%{name_contains}%")
        if origin:
            clauses.append("origin=?")
            params.append(origin.value)
        sql = "SELECT * FROM nodes WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_node_from_row(r) for r in rows]

    def resolve(self, ref: str) -> Node | None:
        """Resolve a user-supplied reference to a node: exact id, exact name, then substring."""
        node = self.get_node(ref)
        if node and not node.deleted:
            return node
        matches = self.find_nodes(name=ref)
        if matches:
            return matches[0]
        matches = self.find_nodes(name_contains=ref)
        if matches:
            # Prefer the shortest name (closest match), services before workloads.
            matches.sort(key=lambda n: (0 if n.type == "Service" else 1, len(n.name)))
            return matches[0]
        return None

    def tombstone_node(self, node_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET deleted=1, last_seen=? WHERE id=?", (now_iso(), node_id)
            )
            self._conn.commit()

    def sync_live_absent(self, adapter: str, seen_ids: set[str]) -> list[str]:
        """After a live refresh, tombstone live-only nodes from this adapter that vanished.

        Nodes that were both declared and observed drop back to static origin
        instead of being deleted.
        """
        gone: list[str] = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE source_adapter=? AND deleted=0", (adapter,)
            ).fetchall()
            for row in rows:
                node = _node_from_row(row)
                if node.id in seen_ids:
                    continue
                if node.origin == Origin.LIVE:
                    self._conn.execute(
                        "UPDATE nodes SET deleted=1, last_seen=? WHERE id=?", (now_iso(), node.id)
                    )
                    gone.append(node.id)
                elif node.origin == Origin.BOTH:
                    attrs = {**node.attrs, "live_state": "absent"}
                    self._conn.execute(
                        "UPDATE nodes SET origin=?, attrs=?, last_seen=? WHERE id=?",
                        (Origin.STATIC.value, json.dumps(attrs, default=str), now_iso(), node.id),
                    )
                    gone.append(node.id)
            self._conn.commit()
        return gone

    # -- edges -------------------------------------------------------------

    def upsert_edge(self, edge: Edge) -> Edge:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM edges WHERE source=? AND target=? AND relation=?",
                (edge.source, edge.target, edge.relation.value),
            ).fetchone()
            if row:
                existing = _edge_from_row(row)
                merged = Edge(
                    source=edge.source,
                    target=edge.target,
                    relation=edge.relation,
                    confidence=merge_confidence(existing.confidence, edge.confidence),
                    evidence=list(dict.fromkeys(existing.evidence + edge.evidence)),
                    attrs={**existing.attrs, **edge.attrs},
                    first_seen=existing.first_seen,
                    last_seen=now_iso(),
                )
            else:
                merged = edge.model_copy(update={"last_seen": now_iso()})
            self._conn.execute(
                """INSERT OR REPLACE INTO edges
                   (source, target, relation, confidence, evidence, attrs, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    merged.source,
                    merged.target,
                    merged.relation.value,
                    merged.confidence.value,
                    json.dumps(merged.evidence),
                    json.dumps(merged.attrs, default=str),
                    merged.first_seen,
                    merged.last_seen,
                ),
            )
            self._conn.commit()
            return merged

    def all_edges(self) -> list[Edge]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM edges").fetchall()
        return [_edge_from_row(r) for r in rows]

    def edges_for(self, node_id: str, direction: str = "both") -> list[Edge]:
        if direction == "out":
            sql, params = "SELECT * FROM edges WHERE source=?", (node_id,)
        elif direction == "in":
            sql, params = "SELECT * FROM edges WHERE target=?", (node_id,)
        else:
            sql, params = "SELECT * FROM edges WHERE source=? OR target=?", (node_id, node_id)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_edge_from_row(r) for r in rows]

    # -- bulk + views --------------------------------------------------------

    def apply(self, nodes: list[Node] | None = None, edges: list[Edge] | None = None) -> None:
        for node in nodes or []:
            self.upsert_node(node)
        for edge in edges or []:
            self.upsert_edge(edge)

    def to_networkx(self) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()
        node_ids = set()
        for node in self.all_nodes():
            graph.add_node(node.id, **{"type": node.type, "name": node.name, "node": node})
            node_ids.add(node.id)
        for edge in self.all_edges():
            if edge.source in node_ids and edge.target in node_ids:
                graph.add_edge(edge.source, edge.target, key=edge.relation.value, edge=edge)
        return graph

    # -- kv ------------------------------------------------------------------

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, value))
            self._conn.commit()
