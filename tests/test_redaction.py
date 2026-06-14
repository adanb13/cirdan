import json
from types import SimpleNamespace

from cirdan.access.redaction import REDACTED, redact_obj, redact_text
from cirdan.actions.executor import ActionRecord, _persist
from cirdan.adapters.base import ActionResult, ActionSpec
from cirdan.audit import AuditWriter
from cirdan.graph.schema import Edge, Node, Relation
from cirdan.graph.store import GraphStore
from cirdan.incidents.store import Incident, IncidentStore
from cirdan.telemetry.events import Event, EventStore


def test_redacts_url_credentials():
    out = redact_text("postgres://admin:hunter2@db.internal:5432/app")
    assert "hunter2" not in out
    assert "db.internal" in out


def test_redacts_secret_env_pairs():
    out = redact_text("DATABASE_PASSWORD=supersecret PORT=8080")
    assert "supersecret" not in out
    assert "PORT=8080" in out


def test_redacts_aws_key():
    assert "AKIA" not in redact_text("key id AKIAIOSFODNN7EXAMPLE here")


def test_redact_obj_drops_secret_keys():
    obj = {"api_key": 123, "name": "web", "nested": {"TOKEN": ["zzz"]}}
    out = redact_obj(obj)
    assert out["api_key"] == REDACTED
    assert out["nested"]["TOKEN"] == REDACTED
    assert out["name"] == "web"


def test_redacts_json_style_secret_text():
    out = redact_text('{"token": "abc123", "name": "web"}')
    assert "abc123" not in out
    assert '"token": "[REDACTED]"' in out
    assert '"name": "web"' in out


def test_persistence_boundaries_redact_sensitive_payloads(tmp_path):
    store = GraphStore(tmp_path / "cirdan.db")
    store.upsert_node(
        Node(
            id="service:web",
            type="Service",
            name="web",
            evidence=['config: "token": "node-secret"'],
            attrs={"api_key": 123, "url": "postgres://admin:node-pass@db/app"},
        )
    )
    store.upsert_edge(
        Edge(
            source="service:web",
            target="database:db",
            relation=Relation.CONNECTS_TO,
            evidence=["DATABASE_PASSWORD=edge-secret"],
            attrs={"token": ["edge-secret"]},
        )
    )
    EventStore(store).add(
        Event(
            provider="docker",
            severity="error",
            resource="container:web",
            message='failed with "token": "event-secret"',
            data={"password": {"value": "event-secret"}},
        )
    )
    IncidentStore(store).upsert(
        Incident(
            id="inc-1",
            key="error:web",
            title="DATABASE_PASSWORD=incident-secret",
            evidence=["DATABASE_PASSWORD=incident-secret"],
            diagnosis={"token": 7},
        )
    )
    _persist(
        SimpleNamespace(store=store),
        ActionRecord(
            record_id="act-1",
            spec=ActionSpec(
                id="docker.logs:web",
                node_id="container:web",
                adapter="docker",
                name="logs",
                description="logs",
                argv=["docker", "login", "--password", "action-secret"],
            ),
            result=ActionResult(
                action_id="docker.logs:web",
                ok=False,
                returncode=1,
                stdout='{"token": "action-secret"}',
            ),
        ),
    )
    store.kv_set("cached", json.dumps({"token": 7, "message": "DATABASE_PASSWORD=kv-secret"}))
    audit_path = tmp_path / "audit.jsonl"
    AuditWriter(audit_path).write(
        "test",
        "DATABASE_PASSWORD=audit-secret",
        payload={"token": 7},
    )

    with store.lock:
        rows = []
        rows.extend(store.conn.execute("SELECT evidence, attrs FROM nodes").fetchall())
        rows.extend(store.conn.execute("SELECT evidence, attrs FROM edges").fetchall())
        rows.extend(store.conn.execute("SELECT message, data FROM events").fetchall())
        rows.extend(store.conn.execute("SELECT data FROM incidents").fetchall())
        rows.extend(store.conn.execute("SELECT data FROM actions").fetchall())
        rows.extend(store.conn.execute("SELECT value FROM kv").fetchall())
    combined = "\n".join(str(tuple(row)) for row in rows) + "\n" + audit_path.read_text()
    for secret in (
        "node-secret",
        "node-pass",
        "edge-secret",
        "event-secret",
        "incident-secret",
        "action-secret",
        "kv-secret",
        "audit-secret",
    ):
        assert secret not in combined
    assert REDACTED in combined
