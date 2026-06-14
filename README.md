<!-- mcp-name: io.github.adanb13/cirdan -->

# Cirdan

[![PyPI](https://img.shields.io/pypi/v/cirdanops)](https://pypi.org/project/cirdanops/)
[![npm](https://img.shields.io/npm/v/%40cirdanops%2Fcli?logo=npm)](https://www.npmjs.com/package/@cirdanops/cli)
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

## Quickstart

```bash
uv tool install "cirdanops[all]"   # or npm / brew / curl — see Install below
cirdan setup                       # one command: hook your agents, register MCP,
                                   # arm the responder, build the first map, start the daemon
```

`cirdan setup` walks you through the whole loop and only proposes what's missing, so
it's safe to re-run. Two useful flags:

- `cirdan setup --all`     — run every step without prompting (good for scripts / CI)
- `cirdan setup --system`  — set up machine-level awareness in `~/.cirdan` instead of a repo

That's it — Cirdan is now mapping, watching, and agent-ready. Read on for everything else.

## Install

**From PyPI (recommended):**

```bash
uv tool install "cirdanops[all]"
# or
pipx install "cirdanops[all]"
pip install "cirdanops[all]"
```

**Without Python — npm / Homebrew / curl:**

```bash
npx -y @cirdanops/cli serve-mcp        # run the MCP server, zero install
npm install -g @cirdanops/cli          # or put the `cirdan` command on your PATH
brew install adanb13/tap/cirdan        # macOS / Linux (Homebrew)
curl -LsSf https://raw.githubusercontent.com/adanb13/cirdan/main/packaging/install.sh | sh
# Windows: irm https://raw.githubusercontent.com/adanb13/cirdan/main/packaging/install.ps1 | iex
```

These ship a self-contained binary — no Python 3.11+ required. Supported: macOS (arm64), Linux x64/arm64 (glibc), Windows x64. On Intel Macs, Alpine/musl, or other platforms, use the PyPI install above.

**Then set it up in one command** (see [Quickstart](#quickstart)):

```bash
cirdan setup --system     # map + watch your whole machine, agent-ready
# or, inside a repo:
cirdan install --project
```

Both walk you through the full loop: hook your AI agents, arm the incident responder, build the first map, and start the always-on daemon.

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
| npm (`-g`) | `npm install -g @cirdanops/cli@latest` (or just `npx @cirdanops/cli@latest …`) |
| Homebrew | `brew upgrade cirdan` |
| curl installer | re-run the install script (it fetches the latest) |
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
cirdan query "what is running right now?"
cirdan query "what depends on postgres?"
cirdan query "what broke in the last hour?"
cirdan query "what can the agent do here?"
cirdan show "state"                 # workload state table
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
cirdan setup                        # set up all of Cirdan: agents + MCP + responder + map + daemon
cirdan setup --all                  # same, non-interactive (every step, no prompts)
cirdan setup --system               # set up machine-level scope in ~/.cirdan
cirdan install --project            # alias for the project-scope guided setup
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

Once `~/.cirdan/` exists, the flag is mostly optional: a command run outside any cirdan project (no `cirdan.yaml`, `cirdan-out/`, or registered `.mcp.json`) falls back to the system scope automatically — with a one-line stderr notice — instead of scattering `cirdan-out/` directories or failing in unwritable locations. Inside a project, project scope always wins.

`cirdan setup --system` also hooks your agents at the user level (instruction files whose examples carry `--system`) and registers Cirdan as a **user-scope MCP server** for the agent CLIs that support it — Claude Code and Codex through their own `mcp add`, VS Code through `code --add-mcp`, and Cursor, Gemini, Windsurf, opencode, and Goose through their global config — so the MCP tools are available in every project without per-repo setup.

## Agent integration

```bash
cirdan install --project             # full guided setup (recommended, see below)
cirdan setup                         # re-run the guided setup anytime, step by step
cirdan install --platform claude     # .claude/skills/cirdan/SKILL.md + CLAUDE.md + .mcp.json
cirdan install --platform codex      # AGENTS.md + .codex/cirdan.md
cirdan install --platform cursor     # .cursor/rules/cirdan.mdc + .cursor/mcp.json
cirdan install --platform gemini     # GEMINI.md
cirdan install --platform vscode     # .github/copilot-instructions.md + .vscode/mcp.json
cirdan install --platform windsurf   # .windsurf/rules/cirdan.md  (MCP: global config)
cirdan install --platform roo        # .roo/rules/cirdan.md + .roo/mcp.json
cirdan install --platform cline      # .clinerules/cirdan.md  (MCP: editor UI)
cirdan install --platform opencode   # AGENTS.md + opencode.json
cirdan install --platform goose      # .goosehints  (MCP: global config)
cirdan install --platform generic    # .agents/skills/cirdan/SKILL.md + AGENTS.md
```

`cirdan install --project` is a guided setup that leaves Cirdan in full use, not just documented:

1. **detects the agents on your machine** (claude/codex/cursor/gemini/vscode/windsurf/opencode/goose config or CLIs) and writes instruction files for exactly those (plus generic `AGENTS.md`)
2. **registers the MCP server** in `.mcp.json`
3. **arms the incident responder** so daemon alerts route to your agent
4. **runs the first map** (graph + artifacts in `cirdan-out/`)
5. **starts the always-on daemon** (detached; `cirdand status` / `cirdand stop`)

Steps 3–5 are prompted (default yes) or driven by flags for scripts: `--responder/--no-responder`, `--map/--no-map`, `--daemon/--no-daemon`, `--all-platforms`. Re-run any of it later with `cirdan setup`, which shows each step's current state and only proposes what's missing.

Installs are idempotent and never touch content outside Cirdan's own marker block. MCP tools include `query_infra_graph`, `get_node`, `get_neighbors`, `shortest_path`, `get_recent_errors`, `get_logs`, `get_state`, `list_incidents`, `explain_incident`, `list_available_actions`, `execute_action`, `verify_action`, `generate_view`, and more.

**Supported agents.** First-class auto-registration (native instruction file + MCP where the agent supports a writable config) covers Claude Code, Codex, Cursor, Gemini, VS Code (+ GitHub Copilot), Windsurf, Roo Code, Cline, opencode, and Goose. Any other agent works through the generic `AGENTS.md` + `.agents/skills/cirdan/SKILL.md`. Auto-detection finds claude/codex/cursor/gemini/vscode/windsurf/opencode/goose; pass `--platform roo` or `--platform cline` explicitly (they're VS Code extensions with no CLI to detect, and Cline keeps MCP config in editor storage, so add that one through its UI).

For any MCP client not listed, register Cirdan by hand — no Python required:

```jsonc
// npx form — zero install
{ "command": "npx", "args": ["-y", "@cirdanops/cli", "serve-mcp"] }
// or, if `cirdan` is already on PATH (pip / npm -g / brew / curl install):
{ "command": "cirdan", "args": ["serve-mcp"] }
```

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

## Agents don't just read the graph — they build it

Like Graphify's hybrid model (deterministic tree-sitter lane for code, LLM lane for docs/semantics), Cirdan's graph has two lanes. Adapters extract what's parseable — that's the always-on, token-free lane, labeled `EXTRACTED`. Agents contribute what isn't: relationships described in READMEs and runbooks, implied by code, or known to operators. Contributions require evidence quotes, are capped at `INFERRED`/`AMBIGUOUS` (an agent can never overwrite a deterministic claim), and carry the contributing agent's name.

```bash
cirdan enrich                # hand your agent a brief of what the scanners left
                             # unconnected (isolated nodes, unlinked IaC, docs to read)
cirdan enrich --dry-run      # just see the brief
cirdan graph add-edge payment-worker orders DEPENDS_ON \
  --evidence "ARCHITECTURE.md: 'the worker drains the orders queue nightly'"
```

MCP-connected agents get the same surface as tools: `upsert_node`, `upsert_edge`, `annotate_node`, `get_enrichment_targets`. Contributions appear in `INFRA_REPORT.md` under "Agent-contributed knowledge" and as dashed edges in `infra.html`.

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
