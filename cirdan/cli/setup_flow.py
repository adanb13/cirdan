"""Guided setup steps shared by `cirdan install` and `cirdan setup`.

Each step knows its current status (so setup is re-runnable and only proposes
what's missing) and how to run. The goal: one command leaves Cirdan in full
use — agents hooked, MCP registered, responder armed, graph mapped, daemon
watching.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console

from cirdan.config import load_config

DAEMON_CONFIRM_TIMEOUT = 20.0


class SetupStep:
    name = "step"
    description = ""

    def __init__(self, root: Path, console: Console, status_console: Console):
        self.root = root
        self.console = console
        self.status_console = status_console

    def status(self) -> tuple[bool, str]:
        """(done, human description of current state)."""
        raise NotImplementedError

    def run(self) -> bool:
        raise NotImplementedError


class AgentsStep(SetupStep):
    name = "agents"
    description = "Hook detected AI agents (instruction files)"

    def __init__(self, *args, platforms: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        from cirdan.agents.installer import detect_platforms

        self.platforms = platforms or detect_platforms()

    def status(self) -> tuple[bool, str]:
        existing = [p for p in self.platforms if self._marker(p).exists()]
        missing = [p for p in self.platforms if p not in existing]
        if not missing:
            return True, f"hooked: {', '.join(existing)}"
        return False, f"detected: {', '.join(self.platforms)} (missing: {', '.join(missing)})"

    def _marker(self, platform: str) -> Path:
        markers = {
            "claude": self.root / ".claude" / "skills" / "cirdan" / "SKILL.md",
            "codex": self.root / ".codex" / "cirdan.md",
            "cursor": self.root / ".cursor" / "rules" / "cirdan.mdc",
            "gemini": self.root / "GEMINI.md",
            "generic": self.root / ".agents" / "skills" / "cirdan" / "SKILL.md",
        }
        return markers.get(platform, self.root / "AGENTS.md")

    def run(self) -> bool:
        from cirdan.agents import install as do_install

        written = do_install(platforms=self.platforms, project=True, root=self.root)
        for name, paths in written.items():
            self.console.print(f"  [bold]{name}[/bold]: {', '.join(Path(p).name for p in paths)}")
        return True


class McpStep(SetupStep):
    name = "mcp"
    description = "Register the Cirdan MCP server"

    def status(self) -> tuple[bool, str]:
        path = self.root / ".mcp.json"
        try:
            data = json.loads(path.read_text())
            if "cirdan" in (data.get("mcpServers") or {}):
                return True, "registered in .mcp.json"
        except (OSError, json.JSONDecodeError):
            pass
        return False, "not registered"

    def run(self) -> bool:
        from cirdan.agents.installer import _merge_mcp_json

        _merge_mcp_json(self.root / ".mcp.json")
        self.console.print("  registered cirdan in .mcp.json (command: cirdan serve-mcp)")
        return True


class ResponderStep(SetupStep):
    name = "responder"
    description = "Route daemon alerts to your agent (incident responder)"

    def __init__(self, *args, command: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.command = command

    def status(self) -> tuple[bool, str]:
        config = load_config(self.root)
        if config.responder.command:
            return True, f"armed: {config.responder.command.split()[0]} …"
        return False, "brief-only mode (no agent command)"

    def run(self) -> bool:
        from cirdan.agents.installer import detect_agent_command, write_responder_config

        command = self.command
        if command is None:
            detected = detect_agent_command()
            if detected is None:
                self.console.print("  [yellow]no known agent CLI found[/yellow] — set responder.command manually")
                return False
            _, command = detected
        path = write_responder_config(self.root, command)
        self.console.print(f"  responder.command set in {path.name}: [bold]{command}[/bold]")
        return True


class MapStep(SetupStep):
    name = "map"
    description = "Map the infrastructure (first graph + artifacts)"

    def status(self) -> tuple[bool, str]:
        config = load_config(self.root)
        graph = config.output_dir / "infra.graph.json"
        if graph.is_file():
            age_min = (time.time() - graph.stat().st_mtime) / 60
            return True, f"mapped {int(age_min)} min ago ({graph})"
        return False, "never mapped"

    def run(self) -> bool:
        from cirdan.engine import CirdanEngine

        engine = CirdanEngine.open(str(self.root))
        engine.progress = lambda m: self.status_console.print(f"[dim]  · {m}[/dim]")
        summary = engine.map()
        self.console.print(f"  graph: {summary['nodes']} nodes, {summary['edges']} edges, "
                           f"{len(summary['findings'])} findings → {engine.config.output_dir}")
        return True


class DaemonStep(SetupStep):
    name = "daemon"
    description = "Start the always-on daemon (watch, detect, respond)"

    def _lock_path(self) -> Path:
        return load_config(self.root).output_dir / "cirdand.lock"

    def status(self) -> tuple[bool, str]:
        from cirdan.daemon.lock import holder

        info = holder(self._lock_path())
        if info:
            return True, f"running (pid {info.get('pid')}, since {info.get('started_at')})"
        return False, "not running"

    def run(self) -> bool:
        from cirdan.daemon.lock import holder

        if holder(self._lock_path()):
            self.console.print("  already running")
            return True
        binary = Path(sys.executable).parent / "cirdand"
        if not binary.is_file():
            which = shutil.which("cirdand")
            if which is None:
                self.console.print("  [red]cirdand binary not found[/red]")
                return False
            binary = Path(which)
        config = load_config(self.root)
        out = config.ensure_output_dirs()
        log = (out / "cirdand.log").open("a")
        subprocess.Popen(
            [str(binary), "serve", str(self.root)],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True, cwd=str(self.root),
        )
        deadline = time.time() + DAEMON_CONFIRM_TIMEOUT
        while time.time() < deadline:
            info = holder(self._lock_path())
            if info:
                self.console.print(f"  cirdand running (pid {info.get('pid')}) — "
                                   f"log: {out / 'cirdand.log'} · cirdand status · cirdand stop")
                return True
            time.sleep(0.5)
        self.console.print(f"  [yellow]daemon did not confirm within {DAEMON_CONFIRM_TIMEOUT}s[/yellow] "
                           f"— check {out / 'cirdand.log'}")
        return False


def build_steps(root: Path, console: Console, status_console: Console,
                platforms: list[str] | None = None,
                responder_command: str | None = None) -> list[SetupStep]:
    return [
        AgentsStep(root, console, status_console, platforms=platforms),
        McpStep(root, console, status_console),
        ResponderStep(root, console, status_console, command=responder_command),
        MapStep(root, console, status_console),
        DaemonStep(root, console, status_console),
    ]


def run_guided(root: Path, console: Console, status_console: Console,
               interactive: bool | None = None,
               only: dict[str, bool] | None = None,
               platforms: list[str] | None = None,
               responder_command: str | None = None) -> dict[str, bool]:
    """Run the setup flow.

    `only` maps step name → forced decision (True run / False skip); steps not
    in `only` are prompted when interactive (default-yes when not yet done),
    or skipped when non-interactive.
    """
    import typer

    if interactive is None:
        interactive = sys.stdin.isatty()
    only = only or {}
    results: dict[str, bool] = {}
    for step in build_steps(root, console, status_console, platforms=platforms,
                            responder_command=responder_command):
        done, state = step.status()
        console.print(f"\n[bold]{step.name}[/bold] — {step.description}")
        console.print(f"  current: {state}")
        if step.name in only:
            decision = only[step.name]
        elif interactive:
            decision = typer.confirm("  run this step?", default=not done)
        else:
            decision = False
        if decision:
            try:
                results[step.name] = step.run()
            except Exception as exc:
                console.print(f"  [red]failed:[/red] {exc}")
                results[step.name] = False
        else:
            results[step.name] = done  # unchanged
    return results
