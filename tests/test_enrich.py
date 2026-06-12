import asyncio
import os
import stat
from pathlib import Path

import pytest

from cirdan.engine import CirdanEngine
from cirdan.enrich import build_enrichment_brief, enrichment_targets, run_enrichment
from tests.conftest import FIXTURES, make_access


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """Tmp copy of the k8s-aws fixture with DEFAULT output/db paths, so the
    CLI invoked as a subprocess (the fake agent) shares the same database."""
    import shutil

    from cirdan.config import CirdanConfig

    root = tmp_path / "proj"
    shutil.copytree(FIXTURES / "repos" / "k8s-aws-app", root)
    monkeypatch.setenv("PATH", f"{FIXTURES / 'fake-bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CIRDAN_NO_UPDATE_CHECK", "1")
    eng = CirdanEngine(CirdanConfig(root=str(root)))
    eng._access = make_access()
    eng.store.kv_set("access_context", eng._access.model_dump_json())
    eng.builder().run_static()
    return eng


def test_targets_find_unlinked_iac_and_uncertain(engine):
    targets = enrichment_targets(engine)
    iac_ids = {n.id for n in targets["unlinked_iac"]}
    # SQS queue from main.tf has no edges to anything.
    assert "tf:aws_sqs_queue.payments_jobs" in iac_ids
    # postgres-prod / redis-prod were inferred from env vars.
    uncertain_names = {n.name for n in targets["uncertain"]}
    assert "postgres-prod" in uncertain_names


def test_summarize_targets_omits_zero_categories():
    from cirdan.enrich import summarize_targets

    targets = {"docs": [], "isolated": ["a", "b"], "unlinked_iac": [],
               "pipelines_without_deploys": [], "uncertain": ["c"]}
    assert summarize_targets(targets) == "3 targets: 2 isolated, 1 uncertain"
    empty = {k: [] for k in targets}
    assert summarize_targets(empty) == "0 targets"


def test_brief_contents(engine):
    brief = Path(build_enrichment_brief(engine))
    text = brief.read_text()
    assert "graph-enrichment brief" in text
    assert "tf:aws_sqs_queue.payments_jobs" in text
    assert "Contribution rules" in text
    assert "cirdan graph add-edge" in text
    assert "--system" not in text  # project scope


def test_brief_mentions_system_flag(engine, monkeypatch):
    monkeypatch.setattr(engine.config, "project", "system")
    text = Path(build_enrichment_brief(engine)).read_text()
    assert "--system" in text


def test_run_enrichment_with_fake_agent(engine, tmp_path):
    # Fake agent: reads nothing, contributes one edge via the contrib API in-process
    # through the CLI (same db file).
    import shutil as _shutil

    script = tmp_path / "fake-agent.sh"
    # Prefer the repo venv binary: a PATH `cirdan` may be a stale global install
    # (uv tool / pipx) that predates the command under test. CI has no repo venv
    # and falls back to PATH (pip install -e).
    repo_venv_cirdan = Path(__file__).parent.parent / ".venv" / "bin" / "cirdan"
    venv_cirdan = (
        str(repo_venv_cirdan) if repo_venv_cirdan.is_file() else _shutil.which("cirdan")
    )
    script.write_text(
        f"#!/bin/sh\n{venv_cirdan} graph add-edge checkout-api payments_jobs WRITES_TO "
        f"--evidence 'fake-agent: checkout publishes payment jobs' "
        f"--agent fake --path {engine.config.root_path}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    brief = build_enrichment_brief(engine)
    ok, diff = asyncio.run(run_enrichment(engine, f"{script} {{brief_file}}", brief, timeout=60))
    assert ok, "fake agent should exit 0"
    assert len(diff["edges"]) == 1
    edge = diff["edges"][0]
    assert edge.source == "service:checkout-api"
    assert edge.target == "tf:aws_sqs_queue.payments_jobs"
    assert edge.attrs["source_adapter"] == "agent:fake"


def test_dry_run_spawns_nothing(engine, monkeypatch):
    from typer.testing import CliRunner

    from cirdan.cli.main import app

    monkeypatch.setenv("CIRDAN_NO_UPDATE_CHECK", "1")
    result = CliRunner().invoke(app, [
        "enrich", str(engine.config.root_path),
        "--dry-run", "--command", "definitely-not-a-real-binary {brief_file}",
    ])
    assert result.exit_code == 0, result.output
    assert "Targets:" in result.output
    assert "Would run" in result.output
    assert "definitely-not-a-real-binary" in result.output


def test_mcp_contribution_tools(engine):
    mcp_memory = pytest.importorskip("mcp.shared.memory")
    import json

    from cirdan.mcp.server import build_mcp_server

    server = build_mcp_server(engine)

    async def call(tool, args):
        async with mcp_memory.create_connected_server_and_client_session(server._mcp_server) as client:
            result = await client.call_tool(tool, args)
            return "".join(b.text for b in result.content if hasattr(b, "text"))

    text = asyncio.run(call("upsert_edge", {
        "source": "checkout-api", "target": "postgres-prod", "relation": "READS_FROM",
        "evidence": ["docs: 'checkout reads order state'"],
    }))
    data = json.loads(text)
    assert data["attrs"]["source_adapter"] == "agent:mcp"

    text = asyncio.run(call("upsert_edge", {
        "source": "checkout-api", "target": "nope-xyz", "relation": "READS_FROM",
        "evidence": ["x"],
    }))
    assert "error" in json.loads(text)

    text = asyncio.run(call("get_enrichment_targets", {}))
    assert "unlinked_iac" in json.loads(text)
