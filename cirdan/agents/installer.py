"""Agent installers: teach AI coding agents to use Cirdan as their infrastructure context layer.

Writes are idempotent: shared files (CLAUDE.md, AGENTS.md, GEMINI.md) get a
marker-delimited block that is replaced on reinstall; Cirdan never touches
content outside its own markers.
"""

from __future__ import annotations

import json
from pathlib import Path

START_MARK = "<!-- cirdan:start -->"
END_MARK = "<!-- cirdan:end -->"

INSTRUCTIONS = """\
## Cirdan: infrastructure awareness

Use Cirdan for infrastructure awareness. Before answering questions about
runtime, deployment, services, cloud resources, logs, state, errors,
dependencies, or incidents, query Cirdan first.

Commands:

    cirdan map .                      # fingerprint + build the full infrastructure graph
    cirdan fingerprint .              # what is this system? (runtimes, clouds, IaC, telemetry)
    cirdan query "<question>"         # e.g. "what depends on postgres?", "what broke?"
    cirdan show "<view request>"      # generate a topology/dependency/incident view
    cirdan incidents                  # detect and list incidents
    cirdan explain <incident-or-node> # evidence-backed explanation
    cirdan actions list <node>        # operations possible with current access
    cirdan actions run <action-id>    # execute (recorded + verified); add --yes for writes
    cirdan access .                   # what this session can currently reach

Artifacts land in `cirdan-out/`: `infra.graph.json` (machine-readable graph),
`INFRA_REPORT.md`, `infra.html`, `fingerprint.json`, `access.json`.

Cirdan inherits this agent/session's available access. If this agent can read
files, run shell, use kubectl, use docker, or use cloud credentials, Cirdan can
use the same context. It never escalates beyond it.

If the Cirdan MCP server is registered, prefer its tools
(`query_infra_graph`, `get_node`, `get_recent_errors`, `list_incidents`,
`list_available_actions`, `execute_action`, …) over shelling out.
"""

SKILL_MD = f"""\
---
name: cirdan
description: Map, query, and operate the live infrastructure this session can access. Use for any question about runtime, deployment, services, dependencies, logs, errors, or incidents.
---

# Cirdan

{INSTRUCTIONS}
"""


def _upsert_block(path: Path, content: str) -> Path:
    block = f"{START_MARK}\n{content}\n{END_MARK}"
    if path.is_file():
        text = path.read_text()
        if START_MARK in text and END_MARK in text:
            head, rest = text.split(START_MARK, 1)
            _, tail = rest.split(END_MARK, 1)
            path.write_text(head + block + tail)
            return path
        joiner = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        path.write_text(text + joiner + block + "\n")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block + "\n")
    return path


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _merge_mcp_json(path: Path, key: str = "mcpServers") -> Path:
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text()) or {}
        except json.JSONDecodeError:
            data = {}
    servers = data.setdefault(key, {})
    servers["cirdan"] = {"command": "cirdan", "args": ["serve-mcp"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def install_claude(base: Path, project: bool) -> list[Path]:
    written = [
        _write(base / ".claude" / "skills" / "cirdan" / "SKILL.md", SKILL_MD),
    ]
    if project:
        written.append(_upsert_block(base / "CLAUDE.md", INSTRUCTIONS))
        written.append(_merge_mcp_json(base / ".mcp.json"))
    else:
        written.append(_upsert_block(base / ".claude" / "CLAUDE.md", INSTRUCTIONS))
    return written


def install_codex(base: Path, project: bool) -> list[Path]:
    written = [_upsert_block(base / "AGENTS.md", INSTRUCTIONS)]
    if project:
        written.append(_write(base / ".codex" / "cirdan.md", SKILL_MD))
    return written


def install_cursor(base: Path, project: bool) -> list[Path]:
    rule = (
        "---\ndescription: Use Cirdan for infrastructure context\nalwaysApply: true\n---\n\n"
        + INSTRUCTIONS
    )
    written = [_write(base / ".cursor" / "rules" / "cirdan.mdc", rule)]
    if project:
        written.append(_merge_mcp_json(base / ".cursor" / "mcp.json"))
    return written


def install_gemini(base: Path, project: bool) -> list[Path]:
    return [_upsert_block(base / "GEMINI.md", INSTRUCTIONS)]


def install_generic(base: Path, project: bool) -> list[Path]:
    return [
        _write(base / ".agents" / "skills" / "cirdan" / "SKILL.md", SKILL_MD),
        _upsert_block(base / "AGENTS.md", INSTRUCTIONS),
    ]


PLATFORMS = {
    "claude": install_claude,
    "codex": install_codex,
    "cursor": install_cursor,
    "gemini": install_gemini,
    "generic": install_generic,
}


def install(platforms: list[str] | None = None, project: bool = False,
            root: Path | None = None) -> dict[str, list[str]]:
    """Install agent instructions. project=True targets the repo; otherwise the home dir."""
    base = (root or Path.cwd()) if project else Path.home()
    selected = platforms or list(PLATFORMS)
    written: dict[str, list[str]] = {}
    for name in selected:
        if name not in PLATFORMS:
            raise ValueError(f"unknown platform '{name}'; choose from {', '.join(PLATFORMS)}")
        written[name] = [str(p) for p in PLATFORMS[name](base, project)]
    return written
