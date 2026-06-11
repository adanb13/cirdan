# Cirdan

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

```bash
uv tool install "cirdanops[all]"
# or
pipx install "cirdanops[all]"
pip install "cirdanops[all]"
```

Targeted installs: `cirdanops[mcp]`, `cirdanops[api]`, `cirdanops[terraform]`, or combinations like `cirdanops[terraform,mcp]`. The Docker/Kubernetes/AWS/systemd adapters need no extras — they use the CLIs already on your PATH.

Two commands are installed:

| Command | What it is |
|---|---|
| `cirdan` | Human/agent CLI |
| `cirdand` | Long-running Always ON daemon |

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
cirdan install --project            # teach agents in this repo to use Cirdan
```

## Always ON

```bash
cirdand serve                        # watch, refresh, detect, export — forever
cirdand serve --mcp                  # + MCP (stdio)
cirdand serve --http --mcp --host 0.0.0.0 --port 8090   # shared team server
```

The daemon runs supervised loops: access refresh, fingerprint refresh, graph refresh, Docker/Kubernetes event watching, telemetry ingestion, incident detection, verification, and artifact export. A crashing loop logs and restarts; it never takes the daemon down.

## Agent integration

```bash
cirdan install --project             # all platforms, into this repo
cirdan install --platform claude     # .claude/skills/cirdan/SKILL.md + CLAUDE.md + .mcp.json
cirdan install --platform codex      # AGENTS.md + .codex/cirdan.md
cirdan install --platform cursor     # .cursor/rules/cirdan.mdc + .cursor/mcp.json
cirdan install --platform gemini     # GEMINI.md
cirdan install --platform generic    # .agents/skills/cirdan/SKILL.md + AGENTS.md
```

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

## Actions and verification

Cirdan detects which operations are *technically possible* with the session's access (`docker restart`, `kubectl rollout restart`, `systemctl restart`, …), exposes them as graph-attached capabilities, executes only through the session's own tools, records pre/post state in the audit trail, and verifies the outcome (workload ready, health checks passing, error clusters quiet). There is no separate credential store and no privilege escalation.

## HTTP API

With the `[api]` extra, `cirdand serve --http` exposes `/health`, `/fingerprint`, `/graph`, `/graph/query`, `/services`, `/incidents`, `/actions`, `/views/generate`, `/audit`, and a minimal OTLP/HTTP JSON receiver at `/v1/logs`. Add `--mcp` to mount the MCP server at `/mcp`.

## Configuration

Zero config works. `cirdan.yaml` refines it — see [`cirdan.yaml.example`](cirdan.yaml.example).

## License

Apache-2.0
