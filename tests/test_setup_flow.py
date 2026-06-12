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
