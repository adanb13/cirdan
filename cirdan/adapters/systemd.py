"""Live adapter: systemd units. Discovers failed units plus units declared in the repo."""

from __future__ import annotations

from cirdan.adapters.base import ActionSpec, Adapter, Signal
from cirdan.graph.schema import DiscoveryResult, Node, NodeType, Origin
from cirdan.util import parse_json, run_cmd


class SystemdAdapter(Adapter):
    name = "systemd"
    kind = "live"

    def available(self) -> bool:
        return self.access.can("systemd")

    def fingerprint(self) -> list[Signal]:
        return []  # access context already contributes the systemd signal

    def _list_units(self, *states: str) -> list[dict]:
        res = run_cmd(
            ["systemctl", "list-units", "--type=service", f"--state={','.join(states)}",
             "--output=json", "--no-pager"],
            timeout=10,
        )
        data = parse_json(res.stdout) if res.ok else None
        return data if isinstance(data, list) else []

    def _declared_units(self) -> set[str]:
        return {p.name for p in self.walk_files(".service")}

    def discover(self) -> DiscoveryResult:
        """Full host unit lists are noisy; keep failed units and repo-declared ones."""
        result = DiscoveryResult(adapter=self.name)
        declared = self._declared_units()
        for unit in self._list_units("failed", "running"):
            name = unit.get("unit", "")
            active = unit.get("active")
            failed = active == "failed"
            if not failed and name not in declared:
                continue
            result.nodes.append(
                Node(
                    id=f"systemd:{name}",
                    type=NodeType.SYSTEMD_UNIT.value,
                    name=name,
                    origin=Origin.LIVE,
                    source_adapter=self.name,
                    evidence=[f"systemctl list-units: {name} is {active}"],
                    attrs={"state": "failed" if failed else active, "description": unit.get("description")},
                )
            )
        return result

    def collect_logs(self, scope: str, lines: int = 200) -> list[str]:
        unit = scope.split(":", 1)[-1]
        res = run_cmd(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "cat"], timeout=15)
        return res.stdout.splitlines() if res.ok else []

    def current_state(self, scope: str) -> dict:
        unit = scope.split(":", 1)[-1]
        res = run_cmd(
            ["systemctl", "show", unit, "--property=ActiveState,SubState,MainPID,NRestarts"], timeout=10
        )
        if not res.ok:
            return {"error": res.stderr.strip()}
        return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)

    def actions(self, node: Node) -> list[ActionSpec]:
        if node.type != NodeType.SYSTEMD_UNIT.value:
            return []
        unit = node.name
        specs = [
            ActionSpec(id=f"systemd.status:{unit}", node_id=node.id, adapter=self.name,
                       name="status", description=f"Status of {unit}",
                       argv=["systemctl", "status", unit, "--no-pager"]),
            ActionSpec(id=f"systemd.logs:{unit}", node_id=node.id, adapter=self.name,
                       name="logs", description=f"Recent journal entries for {unit}",
                       argv=["journalctl", "-u", unit, "-n", "200", "--no-pager"]),
        ]
        if self.access.can("file_write"):  # proxy for an interactive session that may sudo
            specs.append(
                ActionSpec(id=f"systemd.restart:{unit}", node_id=node.id, adapter=self.name,
                           name="restart", description=f"Restart {unit}",
                           argv=["systemctl", "restart", unit], writes=True, verify="unit_active")
            )
        return specs
