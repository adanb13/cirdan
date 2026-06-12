<!-- cirdan:start -->
## Cirdan: infrastructure awareness

Use Cirdan for infrastructure awareness. Before answering questions about
runtime, deployment, services, cloud resources, logs, state, errors,
dependencies, or incidents, query Cirdan first.

Commands:

    cirdan map .                      # fingerprint + build the full infrastructure graph
    cirdan fingerprint .              # what is this system? (runtimes, clouds, IaC, telemetry)
    cirdan query "<question>"         # e.g. "what depends on postgres?", "what broke?"
    cirdan show "<view request>"      # generate a topology/dependency/incident view
    cirdan incidents                  # detect and list incidents
    cirdan explain <incident-or-node> # evidence-backed explanation
    cirdan actions list <node>        # operations possible with current access
    cirdan actions run <action-id>    # execute (recorded + verified); add --yes for writes
    cirdan access .                   # what this session can currently reach

Artifacts land in `cirdan-out/`: `infra.graph.json` (machine-readable graph),
`INFRA_REPORT.md`, `infra.html`, `fingerprint.json`, `access.json`.

Cirdan inherits this agent/session's available access. If this agent can read
files, run shell, use kubectl, use docker, or use cloud credentials, Cirdan can
use the same context. It never escalates beyond it.

If the Cirdan MCP server is registered, prefer its tools
(`query_infra_graph`, `get_node`, `get_recent_errors`, `list_incidents`,
`list_available_actions`, `execute_action`, …) over shelling out.

<!-- cirdan:end -->
