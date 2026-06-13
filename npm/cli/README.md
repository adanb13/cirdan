# @cirdanops/cli

Zero-Python standalone build of **[Cirdan](https://github.com/adanb13/cirdan)** — an AI infrastructure cartographer and MCP server. It fingerprints, graphs, and watches the live infrastructure an agent can reach (Docker, Kubernetes, cloud, IaC, telemetry) and exposes it to AI agents over the Model Context Protocol.

This package ships a prebuilt native binary per platform (via `optionalDependencies`), so you do **not** need Python installed.

## Run with npx (no install)

```bash
npx -y @cirdanops/cli serve-mcp     # start the MCP server over stdio
npx -y @cirdanops/cli map .         # fingerprint + graph the current directory
```

## Register as an MCP server

Most agents accept a stdio command. Point them at:

```json
{
  "command": "npx",
  "args": ["-y", "@cirdanops/cli", "serve-mcp"]
}
```

Or install globally so the bare `cirdan` command is on PATH:

```bash
npm install -g @cirdanops/cli
cirdan serve-mcp
```

## Supported platforms

`darwin-arm64`, `darwin-x64`, `linux-x64`, `linux-arm64` (glibc), `win32-x64`.

On Alpine/musl, FreeBSD, or other platforms, install via Python instead:

```bash
pip install cirdanops        # or: pipx install cirdanops / uv tool install cirdanops
```

Full documentation, the CLI reference, and the agent integration guide live in the [main repository](https://github.com/adanb13/cirdan). Licensed under Apache-2.0.
