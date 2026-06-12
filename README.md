# Cirdan

[![PyPI](https://img.shields.io/pypi/v/cirdanops)](https://pypi.org/project/cirdanops/)
[![Python](https://img.shields.io/pypi/pyversions/cirdanops)](https://pypi.org/project/cirdanops/)
[![Downloads](https://static.pepy.tech/badge/cirdanops)](https://pepy.tech/projects/cirdanops)
[![CI](https://github.com/adanb13/cirdan/actions/workflows/ci.yml/badge.svg)](https://github.com/adanb13/cirdan/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/adanb13/cirdan)](LICENSE)
[![Stars](https://img.shields.io/github/stars/adanb13/cirdan?style=social)](https://github.com/adanb13/cirdan/stargazers)

**Cirdan is a standalone AI infrastructure cartographer and operations daemon — Graphify for live infrastructure.**

It installs into AI agents like a skill, but instead of graphing only code, it fingerprints and graphs the live infrastructure the agent can access: Docker, Kubernetes, cloud accounts, IaC, databases, telemetry. It watches that graph continuously, detects incidents, and gives agents (and humans) a structured way to understand and operate the system.

```
Cirdan fingerprints the system.
Cirdan graphs the system.
Cirdan watches the system.
Cirdan lets the agent operate inside the system using the access the agent already has.
Cirdan generates views only when the human asks to see something.
```

## Install

**From PyPI (recommended):**

```bash
uv tool install "cirdanops[all]"
# or
pipx install "cirdanops[all]"
pip install "cirdanops[all]"
```

Targeted installs: `cirdanops[mcp]`, `cirdanops[api]`, `cirdanops[terraform]`, or combinations like `cirdanops[terraform,mcp]`. The Docker/Kubernetes/AWS/systemd adapters need no extras — they use the CLIs already on your PATH.

**Prerequisites by platform** (Python 3.11+ and `uv` or `pipx`):

```bash
# macOS
brew install python@3.12 uv

# Windows
winget install astral-sh.uv

# Ubuntu / Debian
sudo apt install python3.12 python3-pip pipx
# or get uv:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Docker (no Python needed):**

```bash
# run the always-on daemon against your host's Docker, watching the current directory
docker run -d \
  -v "$PWD":/workspace:ro \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -p 127.0.0.1:8090:8090 \
  ghcr.io/adanb13/cirdan
```

(or `docker compose up` with the provided [docker-compose.yml](docker-compose.yml))

**From source:**

```bash
pip install "cirdanops[all] @ git+https://github.com/adanb13/cirdan.git"
```

Two commands are installed:

| Command | What it is |
|---|---|
| `cirdan` | Human/agent CLI |
| `cirdand` | Long-running Always ON daemon |

## Upgrading

Nothing happens automatically — PyPI is pull-only, so an installed version keeps working as-is until you upgrade. New installs always get the latest release. Upgrading is one command, matching however you installed:

| Installed via | Upgrade command |
|---|---|
| `uv tool install` | `uv tool upgrade cirdanops` |
| `pipx install` | `pipx upgrade cirdanops` |
| `pip install` | `pip install -U cirdanops` |
| Docker | `docker pull ghcr.io/adanb13/cirdan:latest` (then restart the container) |

Two operational notes: existing `cirdan-out/` artifacts and the SQLite graph are compatible across releases so far (a schema-version key + migration will land before 1.0). And the CLI prints a single dim line on stderr when a newer release exists (checked at most once a day, interactive terminals only) — silence it with `CIRDAN_NO_UPDATE_CHECK=1`.

## First map

```bash
cirdan map .
```

This fingerprints the environment (repo files **and** live runtimes the session can reach), builds the graph, and writes:

```
cirdan-out/
├── infra.html          # interactive infrastructure map
├── INFRA_REPORT.md     # plain-English report
├── infra.graph.json    # machine-readable graph for agents
├── fingerprint.json    # evidence-backed environment classification
├── access.json         # what this session can currently reach
├── services.json
├── dependencies.json
├── runtime-state.json
├── incidents/          # active.json + history.jsonl
├── views/generated/    # on-demand Agentic UI views
└── audit.jsonl         # everything Cirdan observed, generated, executed, verified
```

## The access model

**Cirdan inherits the agent's execution context.** It is not a permission manager; it is a mirror. If the session can read the repo, run shell, reach `/var/run/docker.sock`, use `kubectl`, or call AWS — Cirdan can use the same context, and nothing more. Run `cirdan access .` to see the live capability report.

## Commands

```bash
cirdan map .                        # full pipeline: fingerprint → graph → artifacts
cirdan fingerprint .                # what is this system? (with confidence + evidence)
cirdan access .                     # capability mirror for the current session
cirdan query "what depends on postgres?"
cirdan query "what broke in the last hour?"
cirdan query "what can the agent do here?"
cirdan show "show me the infrastructure map"
cirdan show "show checkout-api as a dependency graph"
cirdan show "show last night's incidents as a timeline"
cirdan incidents                    # detection pass + list
cirdan explain <incident-id|node>   # evidence-backed explanation
cirdan actions list <node>          # what can be done with current access
cirdan actions run <action-id> --yes
cirdan verify <act-record-id>       # did the system actually recover?
cirdan watch .                      # foreground event stream
cirdan serve-mcp                    # MCP server over stdio
cirdan install --project            # guided setup: agents + MCP + responder + map + daemon
cirdan setup                        # re-run the guided setup, step by step
```

## Always ON

```bash
cirdand serve                        # watch, refresh, detect, export — forever
cirdand serve --mcp                  # + MCP (stdio)
cirdand serve --http --mcp --host 0.0.0.0 --port 8090   # shared team server
```

The daemon runs supervised loops: access refresh, fingerprint refresh, graph refresh, Docker/Kubernetes event watching, telemetry ingestion, incident detection, verification, and artifact export. A crashing loop logs and restarts; it never takes the daemon down.

One instance per scope: a second `cirdand serve` (or `cirdan watch`) against the same scope is refused with the running pid. `cirdan status` / `cirdan stop` (also available on `cirdand`) inspect and shut it down.

## Watch the whole machine

Projects aren't the only scope. The live adapters (Docker, Kubernetes, AWS, systemd) see everything the session can reach regardless of directory — so Cirdan has a **system scope** that watches all of it autonomously, independent of any repo:

```bash
cirdan setup --system        # guided: map everything + start the machine-level daemon
cirdan map --system          # fingerprint + graph everything this session can reach
cirdan query "what broke?" --system
cirdan status --system       # the system daemon
```

System scope lives in `~/.cirdan/` (graph, incidents, briefs, daemon, optional `cirdan.yaml` for responder/webhook settings) and skips repo scanning — declared-vs-live drift stays a per-project concern, while the system daemon watches the live world: every container, cluster, cloud account, and failing unit. Project daemons and the system daemon run independently.

## Agent integration

```bash
cirdan install --project             # full guided setup (recommended, see below)
cirdan setup                         # re-run the guided setup anytime, step by step
cirdan install --platform claude     # .claude/skills/cirdan/SKILL.md + CLAUDE.md + .mcp.json
cirdan install --platform codex      # AGENTS.md + .codex/cirdan.md
cirdan install --platform cursor     # .cursor/rules/cirdan.mdc + .cursor/mcp.json
cirdan install --platform gemini     # GEMINI.md
cirdan install --platform generic    # .agents/skills/cirdan/SKILL.md + AGENTS.md
```

`cirdan install --project` is a guided setup that leaves Cirdan in full use, not just documented:

1. **detects the agents on your machine** (claude/codex/cursor/gemini config or CLIs) and writes instruction files for exactly those (plus generic `AGENTS.md`)
2. **registers the MCP server** in `.mcp.json`
3. **arms the incident responder** so daemon alerts route to your agent
4. **runs the first map** (graph + artifacts in `cirdan-out/`)
5. **starts the always-on daemon** (detached; `cirdand status` / `cirdand stop`)

Steps 3–5 are prompted (default yes) or driven by flags for scripts: `--responder/--no-responder`, `--map/--no-map`, `--daemon/--no-daemon`, `--all-platforms`. Re-run any of it later with `cirdan setup`, which shows each step's current state and only proposes what's missing.

Installs are idempotent and never touch content outside Cirdan's own marker block. MCP tools include `query_infra_graph`, `get_node`, `get_neighbors`, `shortest_path`, `get_recent_errors`, `get_logs`, `get_state`, `list_incidents`, `explain_incident`, `list_available_actions`, `execute_action`, `verify_action`, `generate_view`, and more.

## The graph

Every node and edge carries **evidence** and a **confidence label** (`EXTRACTED`, `INFERRED`, `AMBIGUOUS`, `UNKNOWN`):

```json
{
  "source": "service:checkout-api",
  "target": "database:postgres-prod",
  "relation": "CONNECTS_TO",
  "confidence": "INFERRED",
  "evidence": ["DATABASE_URL references postgres://postgres-prod… in k8s/checkout.yaml"]
}
```

Cirdan maintains a **static graph** (what the repo says should exist: Compose, Kubernetes YAML, Terraform/OpenTofu, Helm, CI, SQL, nginx, systemd units) and a **live graph** (what actually exists: Docker Engine, Kubernetes API, AWS, systemd, Prometheus), merges them, and reports drift — declared-but-not-running, running-but-undeclared, degraded capacity, unhealthy state.

## Incident responder: the agent loop

Detection is automatic; response can be too. When a high/critical incident opens, the daemon writes an **incident brief** (`cirdan-out/incidents/briefs/<id>.md` — evidence, blast radius, available actions, instructions) and, if a responder command is configured, **invokes your agent against it**:

```yaml
# cirdan.yaml — normally wired by `cirdan install --project`, which detects
# claude/codex/gemini/aider on PATH and asks you once
responder:
  command: 'claude -p "Respond to the Cirdan incident brief at {brief_file}"'
  webhook_url: https://hooks.slack.com/services/…   # optional notify on open/resolve
```

The agent investigates and fixes through Cirdan's own tools (`cirdan actions run … --yes`), every action attaches to the incident and is verified, the incident auto-resolves when the condition stays clear, and the whole exchange lands in `audit.jsonl`. Invocations are cooldown-limited per incident condition. Test your setup with `cirdan respond <incident-id> --dry-run`.

## Actions and verification

Cirdan detects which operations are *technically possible* with the session's access (`docker restart`, `kubectl rollout restart`, `systemctl restart`, …), exposes them as graph-attached capabilities, executes only through the session's own tools, records pre/post state in the audit trail, and verifies the outcome (workload ready, health checks passing, error clusters quiet). There is no separate credential store and no privilege escalation.

## HTTP API

With the `[api]` extra, `cirdand serve --http` exposes `/health`, `/fingerprint`, `/graph`, `/graph/query`, `/services`, `/incidents`, `/actions`, `/views/generate`, `/audit`, and a minimal OTLP/HTTP JSON receiver at `/v1/logs`. Add `--mcp` to mount the MCP server at `/mcp`.

## Configuration

Zero config works. `cirdan.yaml` refines it — see [`cirdan.yaml.example`](cirdan.yaml.example).

## License

Apache-2.0
