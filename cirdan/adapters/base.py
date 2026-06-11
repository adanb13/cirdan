"""Adapter interface. Every infrastructure integration implements this surface.

Static adapters read declared infrastructure from the repo; live adapters
observe the running system through whatever access the current agent/session
already has (CLIs, sockets, HTTP endpoints).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from pydantic import BaseModel, Field

from cirdan.access.context import AccessContext
from cirdan.config import CirdanConfig
from cirdan.graph.schema import DiscoveryResult, Node
from cirdan.util import now_iso, run_cmd

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".terraform", "cirdan-out", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".next", "vendor", ".idea", ".vscode",
}
MAX_SCAN_FILES = 20000


class Signal(BaseModel):
    """Evidence that a given system exists in this environment."""

    system: str
    weight: float  # 0..1, combined as 1 - prod(1 - w)
    evidence: str


class ActionSpec(BaseModel):
    id: str
    node_id: str
    adapter: str
    name: str
    description: str
    argv: list[str]
    writes: bool = False
    verify: str | None = None  # verification check to run after execution


class ActionResult(BaseModel):
    action_id: str
    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    executed_at: str = Field(default_factory=now_iso)


class Adapter:
    name: str = "base"
    kind: str = "static"  # "static" or "live"

    def __init__(self, config: CirdanConfig, access: AccessContext):
        self.config = config
        self.access = access
        self.root = config.root_path

    def available(self) -> bool:
        return True

    def fingerprint(self) -> list[Signal]:
        return []

    def discover(self) -> DiscoveryResult:
        return DiscoveryResult(adapter=self.name)

    def collect_logs(self, scope: str, lines: int = 200) -> list[str]:
        return []

    def current_state(self, scope: str) -> dict:
        return {}

    def actions(self, node: Node) -> list[ActionSpec]:
        return []

    def execute(self, spec: ActionSpec) -> ActionResult:
        res = run_cmd(spec.argv, timeout=60)
        return ActionResult(
            action_id=spec.id,
            ok=res.ok,
            returncode=res.returncode,
            stdout=res.stdout[-20000:],
            stderr=res.stderr[-20000:],
        )

    async def watch(self) -> AsyncIterator[dict]:
        return
        yield  # pragma: no cover

    # -- shared scanning helpers --------------------------------------------

    def walk_files(self, *suffixes: str, names: tuple[str, ...] = ()) -> Iterator[Path]:
        count = 0
        for path in self._walk(self.root):
            count += 1
            if count > MAX_SCAN_FILES:
                return
            if suffixes and path.suffix.lower() in suffixes:
                yield path
            elif names and path.name.lower() in names:
                yield path

    def _walk(self, base: Path) -> Iterator[Path]:
        try:
            entries = sorted(base.iterdir())
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.is_dir():
                if entry.name in IGNORE_DIRS or entry.name.startswith(".git"):
                    continue
                yield from self._walk(entry)
            elif entry.is_file():
                yield entry

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)
