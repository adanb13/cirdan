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

_INSTRUCTIONS_TEMPLATE = """\
## Cirdan: infrastructure awareness

Use Cirdan for infrastructure awareness. Before answering questions about
runtime, deployment, services, cloud resources, logs, state, errors,
dependencies, or incidents, query Cirdan first.

Commands:

    cirdan map{map_arg}                  # fingerprint + build the full infrastructure graph
    cirdan fingerprint{dot}              # what is this system? (runtimes, clouds, IaC, telemetry)
    cirdan query "<question>"{flag}      # e.g. "what is running?", "what depends on postgres?", "what broke?"
    cirdan show "<view request>"{flag}   # generate a view; "state" = workload state table
    cirdan incidents{flag}               # detect and list incidents
    cirdan explain <incident-or-node>{flag}  # evidence-backed explanation
    cirdan actions list <node>{flag}     # operations possible with current access
    cirdan actions run <action-id>{flag} # execute (recorded + verified); add --yes for writes
    cirdan access{dot}                   # what this session can currently reach

The graph is writable: when you learn a relationship from docs or code that
Cirdan's scanners missed, contribute it (evidence required, recorded as INFERRED):

    cirdan graph add-edge <source> <target> CONNECTS_TO --evidence "README.md: '…'"{flag}
    cirdan graph add-node queue:orders --type Queue --evidence "docs/arch.md: '…'"{flag}
    cirdan enrich --dry-run{flag}        # see what the scanners left unconnected

Artifacts land in `{outdir}/`: `infra.graph.json` (machine-readable graph),
`INFRA_REPORT.md`, `infra.html`, `fingerprint.json`, `access.json`.
{scope_note}
Cirdan inherits this agent/session's available access. If this agent can read
files, run shell, use kubectl, use docker, or use cloud credentials, Cirdan can
use the same context. It never escalates beyond it.

If the Cirdan MCP server is registered, prefer its tools
(`query_infra_graph`, `get_node`, `get_recent_errors`, `list_incidents`,
`list_available_actions`, `execute_action`, …) over shelling out.
"""

_SYSTEM_SCOPE_NOTE = """
These examples target the machine-level scope (`--system`, graph in ~/.cirdan).
Inside a repo that has its own cirdan project (cirdan.yaml or cirdan-out/),
drop the --system flag to use that project's graph.
"""


def instructions(project: bool = True) -> str:
    return _INSTRUCTIONS_TEMPLATE.format(
        map_arg=" ." if project else " --system",
        dot=" ." if project else "",
        flag="" if project else " --system",
        outdir="cirdan-out" if project else "~/.cirdan",
        scope_note="" if project else _SYSTEM_SCOPE_NOTE,
    )


def skill_md(project: bool = True) -> str:
    return f"""\
---
name: cirdan
description: Map, query, and operate the live infrastructure this session can access. Use for any question about runtime, deployment, services, dependencies, logs, errors, or incidents.
---

# Cirdan

{instructions(project)}
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


def _merge_mcp_json(path: Path, key: str = "mcpServers", args: list[str] | None = None) -> Path:
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text()) or {}
        except json.JSONDecodeError:
            data = {}
    servers = data.setdefault(key, {})
    servers["cirdan"] = {"command": "cirdan", "args": args or ["serve-mcp"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def install_claude(base: Path, project: bool) -> list[Path]:
    written = [
        _write(base / ".claude" / "skills" / "cirdan" / "SKILL.md", skill_md(project)),
    ]
    if project:
        written.append(_upsert_block(base / "CLAUDE.md", instructions(project)))
        written.append(_merge_mcp_json(base / ".mcp.json"))
    else:
        written.append(_upsert_block(base / ".claude" / "CLAUDE.md", instructions(project)))
    return written


def install_codex(base: Path, project: bool) -> list[Path]:
    written = [_upsert_block(base / "AGENTS.md", instructions(project))]
    if project:
        written.append(_write(base / ".codex" / "cirdan.md", skill_md(project)))
    return written


def install_cursor(base: Path, project: bool) -> list[Path]:
    rule = (
        "---\ndescription: Use Cirdan for infrastructure context\nalwaysApply: true\n---\n\n"
        + instructions(project)
    )
    written = [_write(base / ".cursor" / "rules" / "cirdan.mdc", rule)]
    if project:
        written.append(_merge_mcp_json(base / ".cursor" / "mcp.json"))
    return written


def install_gemini(base: Path, project: bool) -> list[Path]:
    return [_upsert_block(base / "GEMINI.md", instructions(project))]


def install_generic(base: Path, project: bool) -> list[Path]:
    return [
        _write(base / ".agents" / "skills" / "cirdan" / "SKILL.md", skill_md(project)),
        _upsert_block(base / "AGENTS.md", instructions(project)),
    ]


PLATFORMS = {
    "claude": install_claude,
    "codex": install_codex,
    "cursor": install_cursor,
    "gemini": install_gemini,
    "generic": install_generic,
}


# How we recognize that an agent platform is in use on this machine:
# its CLI on PATH or its config directory in $HOME.
_PLATFORM_MARKERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # platform: ((binaries), (home-relative dirs))
    "claude": (("claude",), (".claude",)),
    "codex": (("codex",), (".codex",)),
    "cursor": (("cursor-agent", "cursor"), (".cursor",)),
    "gemini": (("gemini",), (".gemini",)),
}


# User-scope MCP registration: make `cirdan serve-mcp --system` available in
# every project. CLI-owned configs (claude, codex) go through the platform's
# own CLI; JSON configs (gemini, cursor) are merged directly.
MCP_USER_PLATFORMS = ("claude", "codex", "cursor", "gemini")

_SYSTEM_MCP_ARGS = ("serve-mcp", "--system")


def _json_has_cirdan(path: Path, key: str = "mcpServers") -> bool:
    try:
        data = json.loads(path.read_text())
        return isinstance(data, dict) and "cirdan" in (data.get(key) or {})
    except (OSError, json.JSONDecodeError):
        return False


def _toml_has_cirdan(path: Path) -> bool:
    import tomllib

    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "cirdan" in (data.get("mcp_servers") or {})


def user_mcp_registered(platform: str) -> bool:
    home = Path.home()
    if platform == "claude":
        return _json_has_cirdan(home / ".claude.json")
    if platform == "codex":
        return _toml_has_cirdan(home / ".codex" / "config.toml")
    if platform == "gemini":
        return _json_has_cirdan(home / ".gemini" / "settings.json")
    if platform == "cursor":
        return _json_has_cirdan(home / ".cursor" / "mcp.json")
    return False


def user_mcp_status(platforms: list[str]) -> tuple[list[str], list[str]]:
    """Split the MCP-capable subset of `platforms` into (registered, missing) at user scope."""
    capable = [p for p in platforms if p in MCP_USER_PLATFORMS]
    registered = [p for p in capable if user_mcp_registered(p)]
    return registered, [p for p in capable if p not in registered]


def register_user_mcp(platforms: list[str]) -> dict[str, tuple[bool, str]]:
    """Register cirdan as a user-scope MCP server. Returns platform -> (ok, message)."""
    import shutil

    from cirdan.util import run_cmd

    home = Path.home()
    results: dict[str, tuple[bool, str]] = {}
    for platform in platforms:
        if platform not in MCP_USER_PLATFORMS:
            continue
        if platform in ("claude", "codex"):
            if not shutil.which(platform):
                results[platform] = (True, f"{platform} CLI not on PATH — skipped")
                continue
            scope = ["--scope", "user"] if platform == "claude" else []  # codex config is user-global
            argv = [platform, "mcp", "add", *scope, "cirdan", "--", "cirdan", *_SYSTEM_MCP_ARGS]
            result = run_cmd(argv, timeout=60)
            if result.ok:
                results[platform] = (True, f"registered via `{platform} mcp add`")
            else:
                detail = (result.stderr or result.stdout).strip()[:300] or "failed"
                results[platform] = (False, f"`{platform} mcp add` failed: {detail}")
        elif platform == "gemini":
            path = _merge_mcp_json(home / ".gemini" / "settings.json", args=list(_SYSTEM_MCP_ARGS))
            results[platform] = (True, f"registered in {path}")
        elif platform == "cursor":
            path = _merge_mcp_json(home / ".cursor" / "mcp.json", args=list(_SYSTEM_MCP_ARGS))
            results[platform] = (True, f"registered in {path}")
    return results


def detect_platforms() -> list[str]:
    """Agent platforms actually present on this machine (always includes generic)."""
    import shutil

    found = []
    for name, (binaries, dirs) in _PLATFORM_MARKERS.items():
        if any(shutil.which(b) for b in binaries) or any((Path.home() / d).is_dir() for d in dirs):
            found.append(name)
    found.append("generic")  # AGENTS.md works for everything else (aider, opencode, …)
    return found


# Agent CLIs we know how to invoke headlessly, in preference order. The prompt
# argument may itself contain a {brief_file} placeholder, filled at run time.
AGENT_CLI_INVOCATIONS = [
    ("claude", 'claude -p "{prompt}"'),
    ("codex", 'codex exec "{prompt}"'),
    ("gemini", 'gemini -p "{prompt}"'),
    ("hermes", 'hermes -z "{prompt}"'),
    ("opencode", 'opencode run "{prompt}"'),
    ("cursor-agent", 'cursor-agent -p "{prompt}"'),
    ("copilot", 'copilot -p "{prompt}"'),
    ("qwen", 'qwen -p "{prompt}"'),
    ("goose", 'goose run -t "{prompt}"'),
    ("aider", 'aider --yes --message "{prompt}"'),
]

_RESPONDER_PROMPT = "Respond to the Cirdan incident brief at {brief_file}"
_ENRICH_PROMPT = "Work through the Cirdan graph-enrichment brief at {brief_file}"

AGENT_RESPONDER_COMMANDS = [
    (name, template.format(prompt=_RESPONDER_PROMPT)) for name, template in AGENT_CLI_INVOCATIONS
]

AGENT_ENRICH_COMMANDS = [
    (name, template.format(prompt=_ENRICH_PROMPT)) for name, template in AGENT_CLI_INVOCATIONS
]


def _detect_commands(table: list[tuple[str, str]]) -> list[tuple[str, str]]:
    import shutil

    return [(name, command) for name, command in table if shutil.which(name)]


def detect_enrich_commands() -> list[tuple[str, str]]:
    """All agent CLIs on PATH we can point at an enrichment brief, in preference order."""
    return _detect_commands(AGENT_ENRICH_COMMANDS)


def detect_enrich_command() -> tuple[str, str] | None:
    detected = detect_enrich_commands()
    return detected[0] if detected else None


def detect_agent_commands() -> list[tuple[str, str]]:
    """All agent CLIs on PATH we can wire as incident responders, in preference order."""
    return _detect_commands(AGENT_RESPONDER_COMMANDS)


def detect_agent_command() -> tuple[str, str] | None:
    """First agent CLI on PATH that we know how to invoke for incident response."""
    detected = detect_agent_commands()
    return detected[0] if detected else None


def write_responder_config(root: Path, command: str) -> Path:
    """Set responder.command in <root>/cirdan.yaml, preserving any other settings."""
    return _merge_yaml_section(root, "responder", enabled=True, command=command)


def write_enrich_config(root: Path, command: str) -> Path:
    """Set enrich.command in <root>/cirdan.yaml, preserving any other settings."""
    return _merge_yaml_section(root, "enrich", command=command)


def _merge_yaml_section(root: Path, section: str, **values) -> Path:
    import yaml

    path = root / "cirdan.yaml"
    data: dict = {}
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            data = {}
    data.setdefault(section, {}).update(values)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


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
