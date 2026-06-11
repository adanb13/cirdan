"""Static adapter: systemd unit files committed to the repo."""

from __future__ import annotations

import configparser

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import DiscoveryResult, Node, NodeType, Origin


class SystemdUnitsAdapter(Adapter):
    name = "systemd-units"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read")

    def _units(self) -> list[tuple[str, configparser.ConfigParser]]:
        out = []
        for path in self.walk_files(".service"):
            parser = configparser.ConfigParser(strict=False, interpolation=None)
            try:
                parser.read_string(path.read_text())
            except (configparser.Error, OSError):
                continue
            if parser.has_section("Service"):
                out.append((self.rel(path), parser))
        return out

    def fingerprint(self) -> list[Signal]:
        units = self._units()
        if not units:
            return []
        return [Signal(system="systemd", weight=0.4, evidence=f"{len(units)} systemd unit files in repo")]

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        for rel, parser in self._units():
            name = rel.rsplit("/", 1)[-1]
            attrs = {"unit_file": rel}
            if parser.has_option("Service", "ExecStart"):
                attrs["exec_start"] = parser.get("Service", "ExecStart")
            result.nodes.append(
                Node(
                    id=f"systemd:{name}",
                    type=NodeType.SYSTEMD_UNIT.value,
                    name=name,
                    origin=Origin.STATIC,
                    source_adapter=self.name,
                    evidence=[f"unit file {rel}"],
                    attrs=attrs,
                )
            )
        return result
