import os
from pathlib import Path

import pytest

from cirdan.config import STATIC_ADAPTER_NAMES, CirdanConfig
from cirdan.engine import CirdanEngine
from tests.conftest import FIXTURES, make_access


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    return home


def test_system_config_shape(fake_home):
    config = CirdanConfig.system()
    assert config.root_path == fake_home
    assert config.output_dir == fake_home / ".cirdan"
    assert config.project == "system"
    assert set(STATIC_ADAPTER_NAMES) <= set(config.adapters.disabled)


def test_system_config_reads_user_settings(fake_home):
    (fake_home / ".cirdan").mkdir()
    (fake_home / ".cirdan" / "cirdan.yaml").write_text(
        "responder:\n  command: myagent {brief_file}\n"
    )
    config = CirdanConfig.system()
    assert config.responder.command == "myagent {brief_file}"
    assert config.output_dir == fake_home / ".cirdan"


def test_system_map_live_only(fake_home):
    engine = CirdanEngine.open(system=True)
    engine._access = make_access(docker_read=True, docker_write=True)
    engine.store.kv_set("access_context", engine._access.model_dump_json())
    summary = engine.map(live=True)

    # Live containers discovered (fake docker), no static repo nodes.
    assert engine.store.get_node("container:compose-app-api-1") is not None
    static_adapters_ran = [name for name in summary["adapters"] if name in STATIC_ADAPTER_NAMES]
    assert static_adapters_ran == []

    # Everything lands under ~/.cirdan.
    assert (fake_home / ".cirdan" / "infra.graph.json").is_file()
    assert engine.config.db_path == fake_home / ".cirdan" / "cirdan.db"


def test_system_and_project_locks_are_independent(fake_home, tmp_path):
    from cirdan.daemon.lock import DaemonLock, holder

    system_lock = DaemonLock(CirdanConfig.system().output_dir / "cirdand.lock")
    project_lock = DaemonLock(tmp_path / "proj-out" / "cirdand.lock")
    system_lock.acquire()
    project_lock.acquire()  # no conflict
    assert holder(system_lock.path) and holder(project_lock.path)
    system_lock.release()
    project_lock.release()


def test_system_brief_mentions_system_flag(fake_home):
    from cirdan.incidents.responder import IncidentResponder
    from cirdan.incidents.store import Incident

    engine = CirdanEngine.open(system=True)
    engine._access = make_access(docker_read=True)
    engine.store.kv_set("access_context", engine._access.model_dump_json())
    incident = Incident(id="inc-test-1", key="k", title="t", affected_nodes=[])
    engine.incidents.upsert(incident)
    brief = Path(IncidentResponder(engine).write_brief(incident)).read_text()
    assert "--system" in brief


# -- scope fallback (commands run outside a project) --------------------------


def _combined_output(result) -> str:
    """CliRunner stdout (+stderr when captured separately), whitespace-normalized
    so assertions survive rich's line wrapping."""
    out = result.output
    try:
        out += result.stderr
    except (ValueError, AttributeError):
        pass
    return " ".join(out.split())


def test_has_project_markers(tmp_path):
    import json

    from cirdan.config import has_project_markers

    empty = tmp_path / "empty"
    empty.mkdir()
    assert has_project_markers(empty) is False

    with_config = tmp_path / "with-config"
    with_config.mkdir()
    (with_config / "cirdan.yaml").write_text("project: x\n")
    assert has_project_markers(with_config) is True

    with_out = tmp_path / "with-out"
    (with_out / "cirdan-out").mkdir(parents=True)
    assert has_project_markers(with_out) is True

    with_mcp = tmp_path / "with-mcp"
    with_mcp.mkdir()
    (with_mcp / ".mcp.json").write_text(json.dumps({"mcpServers": {"cirdan": {"command": "cirdan"}}}))
    assert has_project_markers(with_mcp) is True

    other_mcp = tmp_path / "other-mcp"
    other_mcp.mkdir()
    (other_mcp / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {}}}))
    assert has_project_markers(other_mcp) is False


def test_resolve_scope_falls_back_when_system_available(fake_home, tmp_path):
    from cirdan.config import resolve_scope

    (fake_home / ".cirdan").mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    config, fell_back = resolve_scope(empty)
    assert fell_back is True
    assert config.project == "system"
    assert config.output_dir == fake_home / ".cirdan"


def test_resolve_scope_respects_project_markers(fake_home, tmp_path):
    from cirdan.config import resolve_scope

    (fake_home / ".cirdan").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "cirdan.yaml").write_text("project: x\n")
    config, fell_back = resolve_scope(proj)
    assert fell_back is False
    assert config.root_path == proj


def test_resolve_scope_without_system_setup(fake_home, tmp_path):
    from cirdan.config import resolve_scope

    empty = tmp_path / "empty"
    empty.mkdir()
    config, fell_back = resolve_scope(empty)
    assert fell_back is False
    assert config.project != "system"
    assert config.root_path == empty


def test_resolve_scope_explicit_config_file_never_falls_back(fake_home, tmp_path):
    from cirdan.config import resolve_scope

    (fake_home / ".cirdan").mkdir()
    cfg = tmp_path / "custom.yaml"
    cfg.write_text("project: custom\n")
    empty = tmp_path / "empty"
    empty.mkdir()
    config, fell_back = resolve_scope(empty, config_file=str(cfg))
    assert fell_back is False
    assert config.project == "custom"


def test_engine_open_records_scope_fallback(fake_home, tmp_path):
    (fake_home / ".cirdan").mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    engine = CirdanEngine.open(str(empty))
    assert engine.scope_fallback == str(empty.resolve())
    assert engine.config.output_dir == fake_home / ".cirdan"

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "cirdan.yaml").write_text("project: x\n")
    engine2 = CirdanEngine.open(str(proj))
    assert engine2.scope_fallback is None
    assert engine2.config.root_path == proj


def test_query_cli_falls_back_with_notice(fake_home, tmp_path, monkeypatch):
    """End-to-end regression for the /home crash: `cirdan query` outside a project
    must use the system graph instead of mkdir-ing ./cirdan-out."""
    from typer.testing import CliRunner

    import cirdan.query
    from cirdan.cli.main import app

    monkeypatch.setenv("CIRDAN_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(cirdan.query, "answer_query",
                        lambda engine, question: {"answer": "system scope answer"})
    (fake_home / ".cirdan").mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(app, ["query", "what is this running on?", "--path", str(empty)])
    assert result.exit_code == 0, result.output
    out = _combined_output(result)
    assert "using system scope" in out
    assert "system scope answer" in out
    assert not (empty / "cirdan-out").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_query_cli_clean_error_when_unwritable(fake_home, tmp_path, monkeypatch):
    """No ~/.cirdan to fall back to + unwritable cwd → one clean error line, no traceback."""
    from typer.testing import CliRunner

    from cirdan.cli.main import app

    monkeypatch.setenv("CIRDAN_NO_UPDATE_CHECK", "1")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        result = CliRunner().invoke(app, ["query", "x", "--path", str(ro)])
    finally:
        ro.chmod(0o755)
    assert result.exit_code == 1
    out = _combined_output(result)
    assert "cannot create" in out
    assert "--system" in out
    assert "Traceback" not in out


def test_status_outside_project_reports_system_scope(fake_home, tmp_path):
    from cirdan.cli.main import _lock_path_for

    (fake_home / ".cirdan").mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    lock_path, fell_back = _lock_path_for(str(empty), system=False)
    assert lock_path == fake_home / ".cirdan" / "cirdand.lock"
    assert fell_back is True
