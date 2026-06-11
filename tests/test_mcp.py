import json
import os

import pytest

from cirdan.engine import CirdanEngine
from cirdan.mcp.server import build_mcp_server
from tests.conftest import FIXTURES, make_access

mcp_memory = pytest.importorskip("mcp.shared.memory")


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


async def _call(server, tool: str, args: dict) -> str:
    async with mcp_memory.create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool(tool, args)
        return "".join(block.text for block in result.content if hasattr(block, "text"))


async def test_mcp_tools_listed(engine):
    server = build_mcp_server(engine)
    async with mcp_memory.create_connected_server_and_client_session(server._mcp_server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    expected = {
        "fingerprint_system", "get_access_context", "map_infrastructure", "query_infra_graph",
        "get_node", "get_neighbors", "shortest_path", "list_services", "list_dependencies",
        "list_dependents", "list_public_entrypoints", "list_runtime_workloads",
        "get_recent_errors", "get_logs", "get_state", "list_incidents", "explain_incident",
        "list_available_actions", "execute_action", "verify_action",
        "generate_view", "generate_report", "get_audit",
    }
    assert expected <= tools


async def test_mcp_query_and_node(engine):
    server = build_mcp_server(engine)
    text = await _call(server, "query_infra_graph", {"question": "what depends on postgres?"})
    assert "api" in text
    text = await _call(server, "get_node", {"node_ref": "postgres"})
    data = json.loads(text)
    assert data["node"]["id"] == "database:postgres"
    assert data["edges"]


async def test_mcp_actions_roundtrip(engine):
    server = build_mcp_server(engine)
    text = await _call(server, "list_available_actions", {"node_ref": "container:compose-app-api-1"})
    actions = json.loads(text)
    restart = next(a for a in actions if a["name"] == "restart")
    text = await _call(server, "execute_action", {"action_id": restart["id"]})
    record = json.loads(text)
    assert record["result"]["ok"] is True
    text = await _call(server, "verify_action", {"record_id": record["record_id"]})
    assert json.loads(text)["status"] == "succeeded"


async def test_mcp_generate_view(engine):
    server = build_mcp_server(engine)
    text = await _call(server, "generate_view", {"request": "show api as a dependency graph"})
    data = json.loads(text)
    assert data["view_type"] == "dependency"
    assert len(data["paths"]) == 3
