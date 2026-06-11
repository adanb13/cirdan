"""Static adapter: repo-level evidence — Dockerfiles, docs, runbooks, language hints."""

from __future__ import annotations

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import DiscoveryResult, Node, NodeType, Origin

DOC_NAMES = ("readme.md", "readme.rst", "readme.txt", "runbook.md", "architecture.md", "operations.md")


class LocalFilesAdapter(Adapter):
    name = "local-files"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read")

    def fingerprint(self) -> list[Signal]:
        signals = []
        dockerfiles = [p for p in self.walk_files(names=("dockerfile",))] + [
            p for p in self.walk_files() if p.name.lower().startswith("dockerfile.")
        ]
        if dockerfiles:
            signals.append(
                Signal(system="docker", weight=0.5, evidence=f"{len(dockerfiles)} Dockerfile(s), e.g. {self.rel(dockerfiles[0])}")
            )
        if (self.root / ".gitlab-ci.yml").is_file():
            signals.append(Signal(system="gitlab-ci", weight=0.8, evidence=".gitlab-ci.yml present"))
        return signals

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        repo_name = self.root.name
        docs = [self.rel(p) for p in self.walk_files() if p.name.lower() in DOC_NAMES]
        dockerfiles = [self.rel(p) for p in self.walk_files(names=("dockerfile",))]
        result.nodes.append(
            Node(
                id=f"repo:{repo_name}",
                type=NodeType.REPOSITORY.value,
                name=repo_name,
                origin=Origin.STATIC,
                source_adapter=self.name,
                evidence=[f"repository root {self.root}"],
                attrs={"docs": docs, "dockerfiles": dockerfiles, "is_git": (self.root / ".git").exists()},
            )
        )
        return result
