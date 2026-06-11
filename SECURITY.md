# Security

## Access model

Cirdan deliberately has **no credential store and no permission system of its own**. It operates with exactly the access of the process it runs in:

- It shells out to the CLIs already on PATH (`docker`, `kubectl`, `aws`, `systemctl`, `journalctl`) and uses whatever credentials those tools already have.
- It never escalates: if `kubectl auth can-i patch deployments` says no, Cirdan exposes no Kubernetes write actions.
- The access context (`cirdan access .`, `access.json`) is a *mirror* of current capability, refreshed continuously — not a grant.

Consequences you should be aware of:

- **Running `cirdand` as a privileged service account gives Cirdan that service account's power.** Scope daemon identities the way you would scope any operator.
- Write actions (`cirdan actions run …`) execute real commands (`docker restart`, `kubectl rollout restart`, `systemctl restart`). The CLI prompts before write actions unless `--yes` is passed; the MCP `execute_action` tool relies on the calling agent's own approval flow.
- Every action records pre-state, command, output, post-state, and verification outcome in `cirdan-out/audit.jsonl` and the graph.

## Redaction

Everything written to artifacts (graph exports, reports, views, logs, action output) passes through redaction that scrubs:

- `user:password@` credentials in URLs
- values of secret-shaped keys (`*SECRET*`, `*TOKEN*`, `*PASSWORD*`, `*API_KEY*`, `*PRIVATE_KEY*`, …)
- AWS access key ids, bearer tokens, PEM private key blocks

Redaction is best-effort pattern matching. Treat `cirdan-out/` as sensitive anyway: it describes your infrastructure topology, which is valuable to an attacker on its own. Do not publish it.

## Network behavior

Cirdan makes no calls to any vendor service. Network traffic is limited to the infrastructure APIs the session already uses (Docker socket, Kubernetes API, cloud CLIs, Prometheus endpoint) and, when explicitly served, its own MCP/HTTP listeners. `cirdand serve --http` binds to `127.0.0.1` by default; binding `0.0.0.0` is your decision and the API has no built-in authentication yet — front it with your own auth proxy on shared deployments.

## Reporting

Please report suspected vulnerabilities privately via the repository's security advisory feature rather than a public issue.
