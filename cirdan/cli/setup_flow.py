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

from cirdan.config import CirdanConfig, load_config

DAEMON_CONFIRM_TIMEOUT = 20.0


def choose_agent(console: Console, detected: list[tuple[str, str]], interactive: bool) -> tuple[str, str]:
    """Pick one of the detected (name, command) agent CLIs; preference order wins by default."""
    import typer

    if len(detected) == 1:
        return detected[0]
    if not interactive:
        console.print(f"  using [bold]{detected[0][0]}[/bold] — also detected: "
                      + ", ".join(name for name, _ in detected[1:]))
        return detected[0]
    console.print("  multiple agent CLIs detected:")
    for i, (name, _) in enumerate(detected, 1):
        console.print(f"    {i}. {name}")
    choice = typer.prompt("  use which agent?", default=1, type=int)
    if not 1 <= choice <= len(detected):
        choice = 1
    return detected[choice - 1]


class SetupStep:
    name = "step"
    description = ""
    prompt_default_yes = True
    interactive = False  # set by run_guided; gates sub-prompts inside run()

    def __init__(self, root: Path, console: Console, status_console: Console, system: bool = False):
        self.root = root
        self.console = console
        self.status_console = status_console
        self.system = system

    def _config(self) -> CirdanConfig:
        return CirdanConfig.system() if self.system else load_config(self.root)

    def status(self) -> tuple[bool, str]:
        """(done, human description of current state)."""
        raise NotImplementedError

    def prompt_default(self, done: bool) -> bool:
        return (not done) and self.prompt_default_yes

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
        hooked = [p for p in self.platforms if self._marker(p).exists()]
        pending = [p for p in self.platforms if p not in hooked]
        if not pending:
            return True, f"hooked: {', '.join(hooked)}"
        if hooked:
            return False, f"hooked: {', '.join(hooked)} — not yet hooked: {', '.join(pending)}"
        return False, f"found on this machine: {', '.join(self.platforms)} — none hooked yet"

    def _marker(self, platform: str) -> Path:
        base = Path.home() if self.system else self.root
        markers = {
            "claude": base / ".claude" / "skills" / "cirdan" / "SKILL.md",
            "codex": base / ("AGENTS.md" if self.system else ".codex/cirdan.md"),
            "cursor": base / ".cursor" / "rules" / "cirdan.mdc",
            "gemini": base / "GEMINI.md",
            "generic": base / ".agents" / "skills" / "cirdan" / "SKILL.md",
        }
        return markers.get(platform, base / "AGENTS.md")

    def run(self) -> bool:
        from cirdan.agents import install as do_install

        written = do_install(platforms=self.platforms, project=not self.system, root=self.root)
        for name, paths in written.items():
            self.console.print(f"  [bold]{name}[/bold]: {', '.join(Path(p).name for p in paths)}")
        return True


class McpStep(SetupStep):
    name = "mcp"
    description = "Register the Cirdan MCP server"

    def __init__(self, *args, platforms: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        from cirdan.agents.installer import detect_platforms

        self.platforms = platforms or detect_platforms()

    def status(self) -> tuple[bool, str]:
        if self.system:
            from cirdan.agents.installer import user_mcp_status

            registered, missing = user_mcp_status(self.platforms)
            if not registered and not missing:
                return True, "no MCP-capable agent CLIs detected — register per project: cirdan install --project"
            if not missing:
                return True, f"registered at user scope for: {', '.join(registered)} (available in every project)"
            return False, (f"not registered for: {', '.join(missing)} — "
                           f"will add a user-scope MCP server (cirdan serve-mcp --system)")
        path = self.root / ".mcp.json"
        try:
            data = json.loads(path.read_text())
            if "cirdan" in (data.get("mcpServers") or {}):
                return True, "registered in .mcp.json"
        except (OSError, json.JSONDecodeError):
            pass
        return False, "not registered"

    def run(self) -> bool:
        if self.system:
            from cirdan.agents.installer import register_user_mcp, user_mcp_status

            registered, missing = user_mcp_status(self.platforms)
            for platform in registered:
                self.console.print(f"  [bold]{platform}[/bold]: already registered (user scope)")
            if not missing:
                if not registered:
                    self.console.print("  nothing to do — no MCP-capable agent CLIs detected "
                                       "(register per project: cirdan install --project)")
                return True
            ok = True
            for platform, (success, message) in register_user_mcp(missing).items():
                mark = "" if success else "[red]failed:[/red] "
                self.console.print(f"  [bold]{platform}[/bold]: {mark}{message}")
                ok = ok and success
            return ok
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
        config = self._config()
        if config.responder.command:
            return True, f"armed: {config.responder.command.split()[0]} …"
        return False, "brief-only mode (no agent command)"

    def run(self) -> bool:
        from cirdan.agents.installer import detect_agent_commands, write_responder_config

        command = self.command
        if command is None:
            detected = detect_agent_commands()
            if not detected:
                self.console.print("  [yellow]no known agent CLI found[/yellow] — set responder.command manually")
                return False
            _, command = choose_agent(self.console, detected, self.interactive)
        target = (Path.home() / ".cirdan") if self.system else self.root
        target.mkdir(parents=True, exist_ok=True)
        path = write_responder_config(target, command)
        self.console.print(f"  responder.command set in {path}: [bold]{command}[/bold]")
        return True


class MapStep(SetupStep):
    name = "map"
    description = "Map the infrastructure (first graph + artifacts)"

    def status(self) -> tuple[bool, str]:
        graph = self._config().output_dir / "infra.graph.json"
        if graph.is_file():
            age_min = (time.time() - graph.stat().st_mtime) / 60
            return True, f"mapped {int(age_min)} min ago ({graph})"
        return False, "never mapped"

    def run(self) -> bool:
        from cirdan.engine import CirdanEngine

        engine = CirdanEngine(self._config())
        engine.progress = lambda m: self.status_console.print(f"[dim]  · {m}[/dim]")
        summary = engine.map()
        self.console.print(f"  graph: {summary['nodes']} nodes, {summary['edges']} edges, "
                           f"{len(summary['findings'])} findings → {engine.config.output_dir}")
        return True


class DaemonStep(SetupStep):
    name = "daemon"
    description = "Start the always-on daemon (watch, detect, respond)"

    def _lock_path(self) -> Path:
        return self._config().output_dir / "cirdand.lock"

    def status(self) -> tuple[bool, str]:
        from cirdan.daemon.lock import holder
        from cirdan.util import iso_to_local

        info = holder(self._lock_path())
        if info:
            return True, f"running (pid {info.get('pid')}, since {iso_to_local(info.get('started_at'))})"
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
        config = self._config()
        out = config.ensure_output_dirs()
        log = (out / "cirdand.log").open("a")
        argv = [str(binary), "serve"] + (["--system"] if self.system else [str(self.root)])
        subprocess.Popen(
            argv,
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


class EnrichStep(SetupStep):
    name = "enrich"
    description = "Agent reads docs/configs and fills graph gaps (recommended; costs agent tokens)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._worthwhile = False

    def prompt_default(self, done: bool) -> bool:
        return (not done) and self._worthwhile

    def status(self) -> tuple[bool, str]:
        from cirdan.engine import CirdanEngine
        from cirdan.enrich import enrichment_targets, summarize_targets
        from cirdan.graph.contrib import agent_contributions

        self._worthwhile = False
        engine = CirdanEngine(self._config())
        contributions = agent_contributions(engine)
        total = len(contributions["nodes"]) + len(contributions["edges"])
        if total:
            return True, f"{total} agent contributions in the graph"
        if not engine.store.all_nodes():
            return False, "graph is empty — run the map step first"
        targets = enrichment_targets(engine)
        summary = summarize_targets(targets)
        if summary == "0 targets":
            return True, "nothing to enrich — scanners left no gaps"
        if engine.config.enrich.command:
            self._worthwhile = True
            agent = engine.config.enrich.command.split()[0]
            return False, f"no agent contributions yet — {summary} (agent: {agent}, configured)"
        from cirdan.agents.installer import detect_enrich_commands

        detected = detect_enrich_commands()
        if not detected:
            return False, f"no agent contributions yet — {summary}, but no agent CLI found (cirdan enrich --command …)"
        self._worthwhile = True
        names = ", ".join(name for name, _ in detected)
        label = "agents" if len(detected) > 1 else "agent"
        return False, f"no agent contributions yet — {summary} ({label}: {names})"

    def run(self) -> bool:
        import asyncio

        from cirdan.engine import CirdanEngine
        from cirdan.enrich import (
            build_enrichment_brief, enrichment_targets, run_enrichment, summarize_targets,
        )

        engine = CirdanEngine(self._config())
        summary = summarize_targets(enrichment_targets(engine))
        if summary == "0 targets":
            self.console.print("  nothing to enrich — scanners left no gaps")
            return True
        template = engine.config.enrich.command
        if template is None:
            from cirdan.agents.installer import detect_enrich_commands, write_enrich_config

            detected = detect_enrich_commands()
            if not detected:
                self.console.print("  [yellow]no known agent CLI found[/yellow] — run `cirdan enrich --command …` manually")
                return False
            _, template = choose_agent(self.console, detected, self.interactive)
            target = (Path.home() / ".cirdan") if self.system else self.root
            target.mkdir(parents=True, exist_ok=True)
            path = write_enrich_config(target, template)
            self.console.print(f"  enrich.command saved in {path}")
        brief = build_enrichment_brief(engine)
        agent = template.split()[0]
        self.console.print(f"  running [bold]{agent}[/bold] against the brief — {summary}, up to 15 min")
        self.console.print(f"  brief: {brief}")
        ok, diff = asyncio.run(run_enrichment(engine, template, brief))
        if not diff["nodes"] and not diff["edges"]:
            note = "agent finished but contributed nothing" if ok else "agent failed before contributing"
            self.console.print(f"  {note} — brief and output are in the audit log")
            return ok
        self.console.print(f"  contributed {len(diff['nodes'])} nodes, {len(diff['edges'])} edges:")
        for node in diff["nodes"][:5]:
            self.console.print(f"    + {node.id}")
        for edge in diff["edges"][:5]:
            self.console.print(f"    + {edge.source} —{edge.relation.value}→ {edge.target}")
        return ok


def build_steps(root: Path, console: Console, status_console: Console,
                platforms: list[str] | None = None,
                responder_command: str | None = None,
                system: bool = False) -> list[SetupStep]:
    return [
        AgentsStep(root, console, status_console, system=system, platforms=platforms),
        McpStep(root, console, status_console, system=system, platforms=platforms),
        ResponderStep(root, console, status_console, system=system, command=responder_command),
        MapStep(root, console, status_console, system=system),
        DaemonStep(root, console, status_console, system=system),
        EnrichStep(root, console, status_console, system=system),
    ]


def run_guided(root: Path, console: Console, status_console: Console,
               interactive: bool | None = None,
               only: dict[str, bool] | None = None,
               platforms: list[str] | None = None,
               responder_command: str | None = None,
               system: bool = False) -> dict[str, bool]:
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
                            responder_command=responder_command, system=system):
        step.interactive = interactive
        done, state = step.status()
        console.print(f"\n[bold]{step.name}[/bold] — {step.description}")
        console.print(f"  current: {state}")
        if step.name in only:
            decision = only[step.name]
        elif interactive:
            decision = typer.confirm("  run this step?", default=step.prompt_default(done))
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
