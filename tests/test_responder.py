import asyncio
import os
from pathlib import Path

import pytest

from cirdan.engine import CirdanEngine
from cirdan.incidents.responder import IncidentResponder, render_command
from cirdan.telemetry.clusters import template_key
from cirdan.telemetry.events import Event
from tests.conftest import FIXTURES, make_access


@pytest.fixture
def engine(compose_app, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    compose_app.output.dir = str(tmp_path / "out")
    compose_app.storage.path = str(tmp_path / "cirdan.db")
    eng = CirdanEngine(compose_app)
    eng._access = make_access(docker_read=True, docker_write=True)
    eng.store.kv_set("access_context", eng._access.model_dump_json())
    eng.builder().run_static()
    eng.builder().run_live()
    return eng


def open_incident(engine):
    for i in range(8):
        engine.events.add(
            Event(severity="error", provider="docker", resource="container:compose-app-api-1",
                  service="api", message="ERROR database connection refused",
                  error_family=template_key("ERROR database connection refused")),
        )
    engine.detect_incidents(ingest=False)
    return next(i for i in engine.incidents.list() if i.key.startswith("error_cluster"))


def test_enabled_by_default_brief_only(engine):
    incident = open_incident(engine)
    responder = IncidentResponder(engine)
    assert engine.config.responder.enabled is True
    assert responder.should_respond(incident) is True  # no command needed for briefs

    ok = asyncio.run(responder.invoke(incident))
    assert ok is True
    brief = Path(engine.config.output_dir) / "incidents" / "briefs" / f"{incident.id}.md"
    assert brief.is_file()
    entries = engine.audit.tail(20)
    assert any(e["kind"] == "responder" and "brief ready" in e["summary"] for e in entries)


def test_brief_contents(engine):
    incident = open_incident(engine)
    responder = IncidentResponder(engine)
    text = Path(responder.write_brief(incident)).read_text()
    assert "# Incident:" in text
    assert "Blast radius" in text
    assert "## Available actions" in text
    assert "docker.restart:compose-app-api-1" in text
    assert "## Your task" in text
    assert incident.id in text


def test_severity_and_cooldown_gating(engine):
    incident = open_incident(engine)
    responder = IncidentResponder(engine)
    assert responder.should_respond(incident)
    responder._mark_responded(incident)
    assert not responder.should_respond(incident)  # cooldown

    incident2 = incident.model_copy(update={"key": "other", "severity": "warning"})
    assert not responder.should_respond(incident2)  # severity filter

    engine.config.responder.enabled = False
    assert not responder.should_respond(incident.model_copy(update={"key": "third"}))


def test_invoke_runs_agent_command(engine, tmp_path):
    incident = open_incident(engine)
    marker = tmp_path / "agent-marker"
    engine.config.responder.command = f"cp {{brief_file}} {marker}"
    responder = IncidentResponder(engine)
    ok = asyncio.run(responder.invoke(incident))
    assert ok is True
    assert marker.is_file()
    assert "# Incident:" in marker.read_text()  # agent received the brief path

    updated = engine.incidents.get(incident.id)
    assert any("agent invoked" in h.get("note", "") for h in updated.history)
    entries = engine.audit.tail(20)
    assert any(e["kind"] == "responder" and "exited 0" in e["summary"] for e in entries)


def test_invoke_failure_is_recorded(engine):
    incident = open_incident(engine)
    engine.config.responder.command = "false"
    responder = IncidentResponder(engine)
    ok = asyncio.run(responder.invoke(incident))
    assert ok is False
    entries = engine.audit.tail(20)
    assert any(e["kind"] == "responder" and "exited 1" in e["summary"] for e in entries)


def test_render_command_no_shell_injection(engine):
    incident = open_incident(engine)
    incident.title = "boom; rm -rf /"
    argv = render_command('echo {title}', incident, "/tmp/brief.md")
    assert argv == ["echo", "boom;", "rm", "-rf", "/"]  # split as args, never a shell string


async def test_daemon_invokes_responder(engine, tmp_path):
    from cirdan.daemon import CirdanDaemon

    marker = tmp_path / "daemon-responded"
    engine.config.responder.command = f"touch {marker}"
    engine.config.daemon.incident_interval = 1.0
    engine.refresh_access = lambda: engine._access  # type: ignore[method-assign]
    daemon = CirdanDaemon(engine)
    open_incident(engine)  # active incident exists before loops start
    await daemon.start()
    try:
        for _ in range(40):
            await asyncio.sleep(0.25)
            if marker.is_file():
                break
        assert marker.is_file(), "daemon should have invoked the responder command"
    finally:
        await daemon.stop()


def test_install_writes_responder_config(tmp_path):
    from cirdan.agents.installer import write_responder_config

    (tmp_path / "cirdan.yaml").write_text("project: demo\n")
    write_responder_config(tmp_path, 'claude -p "x {brief_file}"')
    import yaml

    data = yaml.safe_load((tmp_path / "cirdan.yaml").read_text())
    assert data["project"] == "demo"  # existing settings preserved
    assert data["responder"]["enabled"] is True
    assert "{brief_file}" in data["responder"]["command"]
