# Working on Cirdan

Cirdan is a Python package (`cirdanops`) providing the `cirdan` CLI and `cirdand` daemon. It maps, watches, and operates live infrastructure on behalf of AI agents.

## Setup

```bash
python3 -m venv --without-pip .venv          # ensurepip may be unavailable on Debian/Ubuntu
python3 -m pip --python .venv/bin/python install -e ".[all,dev]"
.venv/bin/python -m pytest tests/ -q
```

## Architecture in one paragraph

`cirdan.engine.CirdanEngine` is the single wiring point used by the CLI (`cirdan/cli/main.py`), MCP server (`cirdan/mcp/server.py`), HTTP API (`cirdan/api/http.py`), and daemon (`cirdan/daemon/server.py`). It owns config, the access context (a probed *mirror* of what the session can do — `cirdan/access/`), the SQLite graph store (`cirdan/graph/store.py`, one file holding nodes/edges/events/incidents/actions), and the audit log. Adapters (`cirdan/adapters/`) implement one interface (`base.py`): static adapters parse repo files into the declared graph; live adapters shell out to CLIs (`docker`, `kubectl`, `aws`, `systemctl`) to build the observed graph, stream events, collect logs, and expose actions. The builder merges both by stable node ids (`service:<name>`), `graph/diff.py` computes drift, `incidents/` turns drift + clustered error events into lifecycle-managed incidents, `actions/` + `verify/` execute and verify operations, and `ui/` renders ViewSpecs to HTML/Markdown/terminal.

## Rules

- **Every graph claim carries evidence and a confidence label** (`EXTRACTED`/`INFERRED`/`AMBIGUOUS`/`UNKNOWN`). New adapters must populate both.
- **Inherited access only.** Adapters must never embed credentials or escalate; availability is gated on the probed `AccessContext`.
- **Everything written to `cirdan-out/` goes through `cirdan/access/redaction.py`.**
- **Subprocess calls use `cirdan.util.run_cmd` with a timeout** so discovery never hangs in restricted sandboxes.
- Live-adapter tests use the canned CLIs in `tests/fixtures/fake-bin/` (prepended to PATH); never require real infrastructure in unit tests.
- New CLI commands import their dependencies inside the function body to keep startup fast.

## Tests

`pytest` with fixtures in `tests/conftest.py` (`compose_app`, `k8s_aws_app` repo fixtures, `make_access` capability factory). The daemon and MCP tests are async (`asyncio_mode=auto`).
