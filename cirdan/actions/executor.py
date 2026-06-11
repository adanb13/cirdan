"""Action discovery and execution through inherited session access.

Cirdan never escalates: an action exists only if the adapter that owns it can
already run the underlying command with the session's credentials. Every
execution is recorded — pre-state, command, output, post-state — in the
actions table, the audit log, and the graph itself.
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field

from cirdan.access.redaction import redact_text
from cirdan.adapters.base import ActionResult, ActionSpec
from cirdan.adapters.registry import get_adapters
from cirdan.engine import CirdanEngine
from cirdan.graph.schema import Confidence, Edge, Node, NodeType, Origin, Relation
from cirdan.util import now_iso


class ActionRecord(BaseModel):
    record_id: str
    spec: ActionSpec
    status: str = "executed"  # executed | verify_passed | verify_failed
    pre_state: dict = Field(default_factory=dict)
    post_state: dict = Field(default_factory=dict)
    result: ActionResult | None = None
    verification: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


def _live_adapters(engine: CirdanEngine) -> dict:
    return {a.name: a for a in get_adapters(engine.config, engine.access, kind="live")}


def list_actions(engine: CirdanEngine, node_ref: str) -> list[ActionSpec]:
    node = engine.store.resolve(node_ref)
    if node is None:
        return []
    specs: list[ActionSpec] = []
    for adapter in _live_adapters(engine).values():
        try:
            specs.extend(adapter.actions(node))
        except Exception:
            continue
    # Workload nodes inherit actions from the runtime resources they create.
    for edge in engine.store.edges_for(node.id, direction="out"):
        if edge.relation != Relation.CREATES:
            continue
        child = engine.store.get_node(edge.target)
        if child:
            for adapter in _live_adapters(engine).values():
                try:
                    specs.extend(adapter.actions(child))
                except Exception:
                    continue
    seen: set[str] = set()
    unique = []
    for spec in specs:
        if spec.id not in seen:
            seen.add(spec.id)
            unique.append(spec)
    return unique


def find_action(engine: CirdanEngine, action_id: str) -> ActionSpec | None:
    """Resolve an action id like docker.restart:my-container back to a live spec."""
    if ":" not in action_id:
        return None
    target = action_id.split(":", 1)[1]
    for ref in (target, target.split("/")[-1]):
        for spec in list_actions(engine, ref):
            if spec.id == action_id:
                return spec
    return None


def execute_action(engine: CirdanEngine, spec: ActionSpec) -> ActionRecord:
    adapter = _live_adapters(engine).get(spec.adapter)
    if adapter is None:
        raise RuntimeError(f"adapter '{spec.adapter}' is not available in this session")
    record = ActionRecord(record_id=f"act-{uuid.uuid4().hex[:8]}", spec=spec)
    try:
        record.pre_state = adapter.current_state(spec.node_id)
    except Exception:
        record.pre_state = {}
    result = adapter.execute(spec)
    result.stdout = redact_text(result.stdout)
    result.stderr = redact_text(result.stderr)
    record.result = result
    try:
        record.post_state = adapter.current_state(spec.node_id)
    except Exception:
        record.post_state = {}
    _persist(engine, record)
    _record_in_graph(engine, record)
    _attach_to_incidents(engine, record)
    engine.audit.write(
        "action",
        f"executed {spec.id} ({'ok' if result.ok else 'failed rc=' + str(result.returncode)})",
        record_id=record.record_id, command=" ".join(spec.argv), writes=spec.writes,
    )
    return record


def _persist(engine: CirdanEngine, record: ActionRecord) -> None:
    with engine.store.lock:
        engine.store.conn.execute(
            "INSERT OR REPLACE INTO actions (id, status, created_at, data) VALUES (?,?,?,?)",
            (record.record_id, record.status, record.created_at, record.model_dump_json()),
        )
        engine.store.conn.commit()


def get_record(engine: CirdanEngine, record_id: str) -> ActionRecord | None:
    with engine.store.lock:
        row = engine.store.conn.execute(
            "SELECT data FROM actions WHERE id=? OR id LIKE ?", (record_id, f"{record_id}%")
        ).fetchone()
    return ActionRecord.model_validate_json(row["data"]) if row else None


def update_record(engine: CirdanEngine, record: ActionRecord) -> None:
    _persist(engine, record)


def list_records(engine: CirdanEngine, limit: int = 50) -> list[ActionRecord]:
    with engine.store.lock:
        rows = engine.store.conn.execute(
            "SELECT data FROM actions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [ActionRecord.model_validate_json(r["data"]) for r in rows]


def _record_in_graph(engine: CirdanEngine, record: ActionRecord) -> None:
    spec, result = record.spec, record.result
    engine.store.upsert_node(
        Node(
            id=f"action:{record.record_id}", type=NodeType.ACTION.value,
            name=f"{spec.name} {spec.node_id}", origin=Origin.DERIVED, source_adapter="actions",
            confidence=Confidence.EXTRACTED,
            evidence=[f"command: {' '.join(spec.argv)}",
                      f"exit code {result.returncode if result else '?'} at {record.created_at}"],
            attrs={"ok": bool(result and result.ok), "writes": spec.writes,
                   "record_id": record.record_id},
        )
    )
    if engine.store.get_node(spec.node_id):
        engine.store.upsert_edge(
            Edge(source=f"action:{record.record_id}", target=spec.node_id,
                 relation=Relation.AFFECTS, confidence=Confidence.EXTRACTED,
                 evidence=[f"action executed against {spec.node_id}"])
        )


def _attach_to_incidents(engine: CirdanEngine, record: ActionRecord) -> None:
    for incident in engine.incidents.list(include_resolved=False):
        if record.spec.node_id in incident.affected_nodes:
            incident.actions.append(record.record_id)
            if record.spec.writes:
                incident.transition("verifying", f"action {record.spec.id} executed")
            engine.incidents.upsert(incident)
