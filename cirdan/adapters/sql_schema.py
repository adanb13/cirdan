"""Static adapter: SQL schema files → table inventories attached as schema config nodes."""

from __future__ import annotations

import re

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import Confidence, DiscoveryResult, Node, NodeType, Origin

CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([a-zA-Z0-9_.]+)", re.IGNORECASE)


class SqlSchemaAdapter(Adapter):
    name = "sql-schema"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read")

    def _schemas(self) -> list[tuple[str, list[str]]]:
        out = []
        for path in self.walk_files(".sql"):
            try:
                tables = CREATE_TABLE_RE.findall(path.read_text())
            except OSError:
                continue
            if tables:
                out.append((self.rel(path), sorted(set(tables))))
        return out

    def fingerprint(self) -> list[Signal]:
        schemas = self._schemas()
        if not schemas:
            return []
        total = sum(len(t) for _, t in schemas)
        return [Signal(system="sql", weight=0.4, evidence=f"{total} tables declared across {len(schemas)} .sql files")]

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        for rel, tables in self._schemas():
            stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            result.nodes.append(
                Node(
                    id=f"schema:{stem}",
                    type=NodeType.CONFIG.value,
                    name=f"sql schema {stem}",
                    origin=Origin.STATIC,
                    source_adapter=self.name,
                    confidence=Confidence.EXTRACTED,
                    evidence=[f"CREATE TABLE statements in {rel}"],
                    attrs={"tables": tables, "file": rel},
                )
            )
        return result
