import asyncio
import os

import pytest

from cirdan.daemon import CirdanDaemon
from cirdan.daemon.lock import DaemonAlreadyRunning, DaemonLock, holder
from cirdan.engine import CirdanEngine
from tests.conftest import FIXTURES, make_access


def test_lock_excludes_second_acquire(tmp_path):
    path = tmp_path / "cirdand.lock"
    first = DaemonLock(path)
    first.acquire()
    assert first.held
    assert holder(path)["pid"] == os.getpid()

    second = DaemonLock(path)
    with pytest.raises(DaemonAlreadyRunning) as excinfo:
        second.acquire()
    assert excinfo.value.pid == os.getpid()
    assert not second.held

    first.release()
    assert holder(path) is None
    second.acquire()
    assert second.held
    second.release()


def test_holder_none_when_free(tmp_path):
    assert holder(tmp_path / "cirdand.lock") is None


@pytest.fixture
def engine(compose_app, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    compose_app.output.dir = str(tmp_path / "out")
    compose_app.storage.path = str(tmp_path / "cirdan.db")
    eng = CirdanEngine(compose_app)
    eng._access = make_access(docker_read=True)
    eng.store.kv_set("access_context", eng._access.model_dump_json())
    eng.refresh_access = lambda: eng._access  # type: ignore[method-assign]
    return eng


async def test_second_daemon_refused(engine):
    daemon1 = CirdanDaemon(engine)
    daemon2 = CirdanDaemon(engine)
    await daemon1.start()
    try:
        with pytest.raises(DaemonAlreadyRunning):
            await daemon2.start()
    finally:
        await daemon1.stop()
    # Lock freed after stop: a new daemon can start.
    daemon3 = CirdanDaemon(engine)
    await daemon3.start()
    try:
        assert daemon3.running
    finally:
        await daemon3.stop()
