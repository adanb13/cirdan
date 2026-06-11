import asyncio
import os

import pytest

from cirdan.daemon import CirdanDaemon
from cirdan.engine import CirdanEngine
from tests.conftest import FIXTURES, make_access


@pytest.fixture
def engine(compose_app, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    compose_app.output.dir = str(tmp_path / "out")
    compose_app.storage.path = str(tmp_path / "cirdan.db")
    compose_app.daemon.incident_interval = 1.0
    compose_app.daemon.export_interval = 2.0
    eng = CirdanEngine(compose_app)
    eng._access = make_access(docker_read=True, docker_write=True)
    eng.store.kv_set("access_context", eng._access.model_dump_json())
    # Pin access so the daemon's refresh keeps the fake capabilities.
    eng.refresh_access = lambda: eng._access  # type: ignore[method-assign]
    return eng


async def test_daemon_watches_and_detects(engine):
    events_seen = []
    daemon = CirdanDaemon(engine, on_event=events_seen.append)
    await daemon.start()
    try:
        assert daemon.running
        # Initial discovery happened before loops started.
        assert engine.store.get_node("service:api") is not None
        assert (engine.config.output_dir / "infra.html").is_file()

        # The fake `docker events` emits a die event; wait for it to propagate
        # through the watch loop and wake the incident loop.
        for _ in range(40):
            await asyncio.sleep(0.25)
            if engine.incidents.list():
                break
        stored = engine.events.recent(window_seconds=120)
        assert any("die" in e.message for e in stored)
        assert engine.incidents.list(), "incident loop should have opened incidents"
        assert any(item["kind"] == "event" for item in events_seen)
    finally:
        await daemon.stop()
    assert not daemon.running


async def test_daemon_loop_crash_is_isolated(engine):
    daemon = CirdanDaemon(engine)

    async def boom():
        raise RuntimeError("synthetic crash")

    async def run():
        daemon._spawn("boom", boom)

    await daemon.start()
    try:
        await run()
        await asyncio.sleep(0.1)
        # The crashing loop logged but the daemon stays up.
        assert daemon.running
        entries = engine.audit.tail(50)
        assert any(e["kind"] == "daemon-error" and "boom" in e["summary"] for e in entries)
    finally:
        await daemon.stop()
