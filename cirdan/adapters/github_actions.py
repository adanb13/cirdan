"""Static adapter: GitHub Actions workflows → pipeline nodes and deploy hints."""

from __future__ import annotations

import re

import yaml

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import Confidence, DiscoveryResult, Node, NodeType, Origin

DEPLOY_HINT_RE = re.compile(
    r"deploy|kubectl\s+apply|helm\s+(upgrade|install)|terraform\s+apply|"
    r"aws\s+ecs\s+update-service|docker\s+push|gcloud\s+run\s+deploy",
    re.IGNORECASE,
)


class GitHubActionsAdapter(Adapter):
    name = "github-actions"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read") and (self.root / ".github" / "workflows").is_dir()

    def _workflows(self) -> list[tuple[str, dict]]:
        out = []
        wf_dir = self.root / ".github" / "workflows"
        if not wf_dir.is_dir():
            return out
        for path in sorted(wf_dir.glob("*.y*ml")):
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue
            if isinstance(data, dict):
                out.append((self.rel(path), data))
        return out

    def fingerprint(self) -> list[Signal]:
        flows = self._workflows()
        if not flows:
            return []
        return [Signal(system="github-actions", weight=0.8, evidence=f"{len(flows)} workflow files in .github/workflows")]

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        for rel, data in self._workflows():
            name = str(data.get("name") or rel.rsplit("/", 1)[-1])
            raw = yaml.safe_dump(data)
            deploys = bool(DEPLOY_HINT_RE.search(raw))
            triggers = data.get("on") or data.get(True) or {}  # YAML may parse `on:` as boolean True
            if isinstance(triggers, dict):
                trigger_list = sorted(str(k) for k in triggers)
            elif isinstance(triggers, list):
                trigger_list = [str(t) for t in triggers]
            else:
                trigger_list = [str(triggers)]
            result.nodes.append(
                Node(
                    id=f"pipeline:{name}",
                    type=NodeType.PIPELINE.value,
                    name=name,
                    origin=Origin.STATIC,
                    source_adapter=self.name,
                    confidence=Confidence.EXTRACTED,
                    evidence=[f"GitHub Actions workflow {rel}"],
                    attrs={
                        "provider": "github-actions",
                        "triggers": trigger_list,
                        "deploys": deploys,
                        "jobs": sorted((data.get("jobs") or {}).keys()),
                    },
                )
            )
        return result
