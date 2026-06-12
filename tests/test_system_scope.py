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
