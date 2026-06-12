"""Incident responder: the push half of the incident loop.

When an incident opens, Cirdan composes an evidence-backed brief on disk and
(optionally) invokes a configured agent command pointed at it — e.g.
`claude -p "Respond to the Cirdan incident brief at {brief_file}"`. The agent
then works through Cirdan's own tools, whose actions attach themselves to the
incident and are verified by the existing pipeline.

Everything here is opt-in (`responder.enabled`), cooldown-limited per incident
condition, and runs without a shell (templates are rendered then shlex-split).
"""

from __future__ import annotations

import asyncio
import shlex
import time

import httpx

from cirdan.engine import CirdanEngine
from cirdan.incidents.store import Incident
from cirdan.util import now_iso

BRIEF_INSTRUCTIONS = """\
## Your task

You are responding to a live infrastructure incident detected by Cirdan.

1. Investigate first:
   - `cirdan explain {incident_id}` for the latest evidence
   - `cirdan query "what depends on <component>?"` for blast radius
   - `cirdan actions run <read-action-id>` for logs/inspect/describe (read actions are safe)
2. If a remediation is warranted, use the available actions listed above:
   - `cirdan actions run <action-id> --yes`
   - Cirdan records the action against this incident automatically.
3. Verify the outcome:
   - `cirdan verify <act-record-id>` (also run automatically for write actions)
   - The incident resolves on its own once the underlying condition stays clear.
4. If no safe action exists, summarize the root cause and what a human should do.

Cirdan inherits this session's access — it can only do what you can already do.
All of your actions are recorded in cirdan-out/audit.jsonl.
"""


def render_command(template: str, incident: Incident, brief_file: str) -> list[str]:
    rendered = template.format(
        brief_file=brief_file,
        incident_id=incident.id,
        title=incident.title,
    )
    return shlex.split(rendered)


async def run_agent_command(
    engine: CirdanEngine, argv: list[str], label: str, timeout: float, subject: str = ""
) -> tuple[bool, str]:
    """Spawn an agent command (no shell), bounded by timeout, fully audited.

    Returns (ok, note) — note is a short human-readable outcome line.
    Shared by the incident responder and `cirdan enrich`.
    """
    started = now_iso()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(engine.config.root_path),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            engine.audit.write("responder", f"{label} for {subject or 'task'} timed out after {timeout}s",
                               command=argv[0])
            return False, f"{label} timed out after {timeout}s"
        output = (stdout or b"").decode(errors="replace")[-4000:]
        ok = proc.returncode == 0
        engine.audit.write(
            "responder",
            f"{label} for {subject or 'task'} exited {proc.returncode}",
            command=argv[0], started=started, output_tail=output[-1000:],
        )
        return ok, f"{label} invoked ({argv[0]}), exit {proc.returncode}"
    except (OSError, ValueError) as exc:
        engine.audit.write("responder", f"{label} for {subject or 'task'} failed to start: {exc}")
        return False, f"{label} failed to start: {exc}"


class IncidentResponder:
    def __init__(self, engine: CirdanEngine):
        self.engine = engine
        self.config = engine.config.responder
        self._semaphore = asyncio.Semaphore(max(1, self.config.max_concurrent))

    # -- gating ----------------------------------------------------------------

    def should_respond(self, incident: Incident) -> bool:
        if not self.config.enabled:
            return False
        if incident.status != "active":
            return False
        if incident.severity not in self.config.severities:
            return False
        key = f"responder:last:{incident.key}"
        last = self.engine.store.kv_get(key)
        if last and (time.time() - float(last)) < self.config.cooldown_seconds:
            return False
        return True

    def _mark_responded(self, incident: Incident) -> None:
        self.engine.store.kv_set(f"responder:last:{incident.key}", str(time.time()))

    # -- brief -------------------------------------------------------------------

    def write_brief(self, incident: Incident) -> str:
        from cirdan.actions.executor import list_actions
        from cirdan.incidents.reports import explain_incident

        lines = [explain_incident(incident, self.engine.store, self.engine.events).rstrip(), ""]
        lines.append("## Available actions (with this session's access)")
        lines.append("")
        any_actions = False
        for node_id in incident.affected_nodes:
            specs = list_actions(self.engine, node_id)
            for spec in specs:
                any_actions = True
                marker = " ⚠ writes" if spec.writes else ""
                lines.append(f"- `{spec.id}`{marker}: {spec.description} (`{' '.join(spec.argv)}`)")
        if not any_actions:
            lines.append("- none discovered; investigate with read tools and report findings")
        lines += ["", BRIEF_INSTRUCTIONS.format(incident_id=incident.id)]
        if self.engine.config.project == "system":
            lines.append("Note: this incident is in the machine-level (system) scope — "
                         "append `--system` to every `cirdan` command above.")

        briefs_dir = self.engine.config.output_dir / "incidents" / "briefs"
        briefs_dir.mkdir(parents=True, exist_ok=True)
        path = briefs_dir / f"{incident.id}.md"
        path.write_text("\n".join(lines))
        return str(path)

    # -- notify ---------------------------------------------------------------------

    def notify(self, incident: Incident, transition: str) -> None:
        if self.config.webhook_url:
            try:
                httpx.post(
                    self.config.webhook_url,
                    json={"transition": transition, "incident": incident.model_dump()},
                    timeout=5,
                )
                self.engine.audit.write("responder", f"webhook notified for {incident.id} ({transition})")
            except httpx.HTTPError as exc:
                self.engine.audit.write("responder", f"webhook failed for {incident.id}: {exc}")

    # -- invoke ------------------------------------------------------------------------

    async def invoke(self, incident: Incident) -> bool:
        """Spawn the configured agent command against a fresh brief. Returns success."""
        async with self._semaphore:
            self._mark_responded(incident)
            brief_file = self.write_brief(incident)
            self.notify(incident, "open")
            if self.config.notify_command:
                await self._run(render_command(self.config.notify_command, incident, brief_file),
                                incident, label="notify-command", timeout=60)
            if not self.config.command:
                # Brief-only mode: surfaced for agents/humans, nothing spawned.
                self.engine.audit.write(
                    "responder",
                    f"incident brief ready for {incident.id} (no responder.command configured; "
                    f"run `cirdan install` or set responder.command to auto-invoke an agent)",
                    brief=brief_file,
                )
                self._note(incident, f"brief written to {brief_file}")
                return True
            argv = render_command(self.config.command, incident, brief_file)
            ok = await self._run(argv, incident, label="agent", timeout=self.config.timeout_seconds)
            return ok

    async def _run(self, argv: list[str], incident: Incident, label: str, timeout: float) -> bool:
        ok, note = await run_agent_command(self.engine, argv, label, timeout, subject=incident.id)
        self._note(incident, note)
        return ok

    def _note(self, incident: Incident, note: str) -> None:
        current = self.engine.incidents.get(incident.id) or incident
        current.history.append({"ts": now_iso(), "status": current.status, "note": note})
        current.updated_at = now_iso()
        self.engine.incidents.upsert(current)
