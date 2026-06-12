import os
import shutil
import signal
import time
from pathlib import Path

import pytest
from rich.console import Console

from cirdan.agents.installer import detect_platforms
from cirdan.cli.setup_flow import build_steps, run_guided
from tests.conftest import FIXTURES

quiet = Console(file=open(os.devnull, "w"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scratch copy of the compose-app fixture with fake CLIs on PATH."""
    root = tmp_path / "proj"
    shutil.copytree(FIXTURES / "repos" / "compose-app", root)
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CIRDAN_NO_UPDATE_CHECK", "1")
    return root


def test_detect_platforms_includes_generic(monkeypatch):
    platforms = detect_platforms()
    assert "generic" in platforms
    # On this machine .claude exists, so claude should be detected.
    if (Path.home() / ".claude").is_dir():
        assert "claude" in platforms


def test_detect_platforms_none(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda *_: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert detect_platforms() == ["generic"]


def test_statuses_before_and_after(project):
    steps = {s.name: s for s in build_steps(project, quiet, quiet)}
    assert steps["mcp"].status()[0] is False
    assert steps["map"].status()[0] is False
    assert steps["responder"].status()[0] is False
    assert steps["daemon"].status()[0] is False

    results = run_guided(
        project, quiet, quiet,
        only={"agents": True, "mcp": True, "responder": False, "map": True, "daemon": False},
        platforms=["generic"],
    )
    assert results["agents"] and results["mcp"] and results["map"]

    steps = {s.name: s for s in build_steps(project, quiet, quiet)}
    assert steps["mcp"].status()[0] is True
    assert steps["map"].status()[0] is True
    assert (project / "cirdan-out" / "infra.graph.json").is_file()
    assert (project / ".agents" / "skills" / "cirdan" / "SKILL.md").is_file()


def test_responder_step_uses_explicit_command(project):
    results = run_guided(
        project, quiet, quiet,
        only={"agents": False, "mcp": False, "responder": True, "map": False, "daemon": False},
        responder_command='myagent --brief {brief_file}',
    )
    assert results["responder"] is True
    assert "myagent" in (project / "cirdan.yaml").read_text()


def test_non_interactive_skips_unflagged(project):
    results = run_guided(project, quiet, quiet, interactive=False, only={"agents": True})
    assert results["agents"] is True
    assert results["map"] is False  # skipped, and not previously done
    assert not (project / "cirdan-out" / "infra.graph.json").exists()


@pytest.mark.skipif(
    not (Path(__file__).parent.parent / ".venv" / "bin" / "cirdand").is_file(),
    reason="cirdand venv binary required",
)
def test_daemon_step_spawns_and_reports_running(project, monkeypatch):
    import cirdan.cli.setup_flow as flow
    from cirdan.daemon.lock import holder

    monkeypatch.setattr(
        flow.shutil, "which",
        lambda name: str(Path(__file__).parent.parent / ".venv" / "bin" / "cirdand")
        if name == "cirdand" else None,
    )
    monkeypatch.setattr(flow.sys, "executable", "/nonexistent/python")  # force which() path
    steps = {s.name: s for s in build_steps(project, quiet, quiet)}
    try:
        assert steps["daemon"].run() is True
        info = holder(project / "cirdan-out" / "cirdand.lock")
        assert info and info.get("pid")
        # Second run: already running, no duplicate.
        assert steps["daemon"].run() is True
    finally:
        info = holder(project / "cirdan-out" / "cirdand.lock")
        if info and info.get("pid"):
            os.kill(int(info["pid"]), signal.SIGTERM)
            deadline = time.time() + 10
            while time.time() < deadline and holder(project / "cirdan-out" / "cirdand.lock"):
                time.sleep(0.3)
    assert holder(project / "cirdan-out" / "cirdand.lock") is None


# -- status wording & system-scope MCP ----------------------------------------


def test_agents_status_wording(project):
    from cirdan.cli.setup_flow import AgentsStep

    step = AgentsStep(project, quiet, quiet, platforms=["claude", "generic"])
    assert step.status() == (False, "found on this machine: claude, generic — none hooked yet")

    generic_marker = project / ".agents" / "skills" / "cirdan" / "SKILL.md"
    generic_marker.parent.mkdir(parents=True)
    generic_marker.write_text("x")
    assert step.status() == (False, "hooked: generic — not yet hooked: claude")

    claude_marker = project / ".claude" / "skills" / "cirdan" / "SKILL.md"
    claude_marker.parent.mkdir(parents=True)
    claude_marker.write_text("x")
    assert step.status() == (True, "hooked: claude, generic")


def test_mcp_step_system_status_wording(tmp_path, monkeypatch):
    from cirdan.cli.setup_flow import McpStep

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    step = McpStep(tmp_path, quiet, quiet, system=True,
                   platforms=["claude", "codex", "cursor", "gemini", "generic"])
    done, msg = step.status()
    assert done is False
    assert "n/a" not in msg
    for name in ("claude", "codex", "cursor", "gemini"):
        assert name in msg

    # No MCP-capable platforms detected → nothing to do, step counts as done.
    none_step = McpStep(tmp_path, quiet, quiet, system=True, platforms=["generic"])
    done, msg = none_step.status()
    assert done is True
    assert "per project" in msg or "--project" in msg


def test_mcp_step_system_status_all_registered(tmp_path, monkeypatch):
    import json

    from cirdan.cli.setup_flow import McpStep

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"cirdan": {"command": "cirdan"}}}))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('[mcp_servers.cirdan]\ncommand = "cirdan"\n')
    (home / ".gemini").mkdir()
    (home / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {"cirdan": {}}}))
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"cirdan": {}}}))

    step = McpStep(tmp_path, quiet, quiet, system=True,
                   platforms=["claude", "codex", "cursor", "gemini", "generic"])
    done, msg = step.status()
    assert done is True
    assert "user scope" in msg


def test_mcp_step_system_run_registers(tmp_path, monkeypatch):
    import json

    import cirdan.util
    from cirdan.cli.setup_flow import McpStep
    from cirdan.util import CmdResult

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **k: f"/usr/bin/{name}" if name in ("claude", "codex") else None)

    calls = []

    def fake_run_cmd(argv, timeout=5.0, input_text=None):
        calls.append(argv)
        return CmdResult(argv=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cirdan.util, "run_cmd", fake_run_cmd)

    # Pre-existing gemini settings must survive the merge.
    (home / ".gemini").mkdir()
    (home / ".gemini" / "settings.json").write_text(json.dumps({"theme": "dark"}))

    step = McpStep(tmp_path, quiet, quiet, system=True,
                   platforms=["claude", "codex", "cursor", "gemini", "generic"])
    assert step.run() is True

    assert ["claude", "mcp", "add", "--scope", "user", "cirdan",
            "--", "cirdan", "serve-mcp", "--system"] in calls
    assert ["codex", "mcp", "add", "cirdan", "--", "cirdan", "serve-mcp", "--system"] in calls

    gemini = json.loads((home / ".gemini" / "settings.json").read_text())
    assert gemini["mcpServers"]["cirdan"]["args"] == ["serve-mcp", "--system"]
    assert gemini["theme"] == "dark"
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["cirdan"]["args"] == ["serve-mcp", "--system"]


def test_setup_summary_appends_system_flag(tmp_path, monkeypatch):
    from cirdan.cli import main as cli_main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    printed = []
    monkeypatch.setattr(cli_main.console, "print",
                        lambda *a, **k: printed.append(" ".join(str(x) for x in a)))

    cli_main._setup_summary(tmp_path, {}, system=True)
    assert "cirdan incidents --system" in printed[-1]

    printed.clear()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    cli_main._setup_summary(project_dir, {}, system=False)
    assert "--system" not in printed[-1]
