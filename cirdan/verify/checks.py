"""Post-action verification: did the system actually recover?"""

from __future__ import annotations

from cirdan.actions.executor import ActionRecord, update_record
from cirdan.adapters.registry import get_adapters
from cirdan.engine import CirdanEngine
from cirdan.util import now_iso


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "result": "pass" if ok else "fail", "detail": detail}


def _container_healthy(state: dict) -> tuple[bool, str]:
    running = state.get("state") == "running"
    health = state.get("health")
    healthy = health in (None, "healthy")
    return running and healthy, f"state={state.get('state')} health={health or 'n/a'}"


def _workload_ready(state: dict) -> tuple[bool, str]:
    status = state.get("status", {}) if isinstance(state.get("status"), dict) else state
    replicas = status.get("replicas")
    ready = status.get("readyReplicas", 0)
    if replicas is None:
        return bool(ready), f"readyReplicas={ready}"
    return int(ready) >= int(replicas), f"ready {ready}/{replicas}"


def _unit_active(state: dict) -> tuple[bool, str]:
    active = state.get("ActiveState")
    return active == "active", f"ActiveState={active} SubState={state.get('SubState')}"


_NAMED_CHECKS = {
    "container_healthy": _container_healthy,
    "workload_ready": _workload_ready,
    "unit_active": _unit_active,
}


def verify_action(engine: CirdanEngine, record: ActionRecord) -> dict:
    """Run the spec's named check plus generic checks; persist the outcome."""
    checks: list[dict] = []
    spec = record.spec

    if record.result is not None:
        checks.append(_check("command_succeeded", record.result.ok,
                             f"exit code {record.result.returncode}"))

    adapter = next(
        (a for a in get_adapters(engine.config, engine.access, kind="live") if a.name == spec.adapter),
        None,
    )
    current_state: dict = {}
    if adapter is not None:
        try:
            current_state = adapter.current_state(spec.node_id)
        except Exception as exc:
            checks.append(_check("state_readable", False, str(exc)))
    if spec.verify and spec.verify in _NAMED_CHECKS and current_state:
        ok, detail = _NAMED_CHECKS[spec.verify](current_state)
        checks.append(_check(spec.verify, ok, detail))

    # No new error events for this resource since the action ran.
    node = engine.store.get_node(spec.node_id)
    service = node.name if node else spec.node_id.split(":", 1)[-1]
    recent_errors = [
        e for e in engine.events.recent(window_seconds=600, severity=("error",), service=service)
        if e.ts >= record.created_at
    ]
    checks.append(_check("no_new_errors", not recent_errors,
                         f"{len(recent_errors)} error events since action"))

    passed = all(c["result"] == "pass" for c in checks)
    record.verification = {
        "verified_at": now_iso(),
        "status": "succeeded" if passed else "failed",
        "checks": checks,
        "summary": (f"{spec.node_id} verified healthy after {spec.name}"
                    if passed else f"{spec.node_id} still failing checks after {spec.name}"),
    }
    record.status = "verify_passed" if passed else "verify_failed"
    update_record(engine, record)
    engine.audit.write("verify", record.verification["summary"],
                       record_id=record.record_id, status=record.verification["status"])

    # Reflect the outcome on any incident this action was attached to.
    for incident in engine.incidents.list(include_resolved=False):
        if record.record_id in incident.actions and incident.status == "verifying":
            if not passed:
                incident.transition("active", f"verification failed for {record.record_id}")
                engine.incidents.upsert(incident)
    return record.verification
