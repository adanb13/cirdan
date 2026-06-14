"""Incident model and persistence (same SQLite file as the graph)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from cirdan.access.redaction import redact_obj
from cirdan.graph.store import GraphStore
from cirdan.util import now_iso

# Lifecycle: suspicious → investigating → active → verifying → resolved.
STATUSES = ("suspicious", "investigating", "active", "verifying", "resolved")


class Incident(BaseModel):
    id: str
    key: str                       # dedup key: kind + primary node
    title: str
    status: str = "suspicious"
    severity: str = "warning"      # warning | high | critical
    started_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    affected_nodes: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    diagnosis: dict = Field(default_factory=dict)   # {summary, confidence}
    actions: list[str] = Field(default_factory=list)  # action ids attempted
    history: list[dict] = Field(default_factory=list)

    def transition(self, status: str, note: str = "") -> None:
        if status == self.status:
            return
        self.status = status
        self.updated_at = now_iso()
        self.history.append({"ts": self.updated_at, "status": status, "note": note})


class IncidentStore:
    def __init__(self, store: GraphStore):
        self.store = store

    def upsert(self, incident: Incident) -> None:
        clean = Incident.model_validate(redact_obj(incident.model_dump()))
        with self.store.lock:
            self.store.conn.execute(
                """INSERT OR REPLACE INTO incidents (id, status, severity, started_at, updated_at, data)
                   VALUES (?,?,?,?,?,?)""",
                (clean.id, clean.status, clean.severity, clean.started_at,
                 clean.updated_at, clean.model_dump_json()),
            )
            self.store.conn.commit()

    def get(self, incident_id: str) -> Incident | None:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT data FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
        if row:
            return Incident.model_validate_json(row["data"])
        # Allow prefix lookup for convenience.
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT data FROM incidents WHERE id LIKE ? ORDER BY started_at DESC", (f"{incident_id}%",)
            ).fetchone()
        return Incident.model_validate_json(row["data"]) if row else None

    def find_open_by_key(self, key: str) -> Incident | None:
        for incident in self.list(include_resolved=False):
            if incident.key == key:
                return incident
        return None

    def list(self, include_resolved: bool = False) -> list[Incident]:
        sql = "SELECT data FROM incidents"
        if not include_resolved:
            sql += " WHERE status != 'resolved'"
        sql += " ORDER BY started_at DESC"
        with self.store.lock:
            rows = self.store.conn.execute(sql).fetchall()
        return [Incident.model_validate_json(r["data"]) for r in rows]

    def export(self, out_dir: Path) -> None:
        inc_dir = out_dir / "incidents"
        inc_dir.mkdir(parents=True, exist_ok=True)
        active = redact_obj([i.model_dump() for i in self.list(include_resolved=False)])
        (inc_dir / "active.json").write_text(json.dumps(active, indent=2) + "\n")
        with (inc_dir / "history.jsonl").open("w") as fh:
            for incident in self.list(include_resolved=True):
                clean = Incident.model_validate(redact_obj(incident.model_dump()))
                fh.write(clean.model_dump_json() + "\n")
