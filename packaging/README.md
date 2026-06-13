# Packaging & distribution

How Cirdan reaches users beyond `pip install cirdanops`. All of these ride on the
zero-Python standalone binary built by [`.github/workflows/release-npm.yml`](../.github/workflows/release-npm.yml),
which on each release builds one PyInstaller binary per platform and:

- publishes the npm packages (`@cirdanops/cli` + `@cirdanops/cli-<platform>`), and
- uploads each binary as a GitHub Release asset named `cirdan-<target>` (e.g.
  `cirdan-darwin-arm64`, `cirdan-win32-x64.exe`).

## Files here

| File | Channel | Notes |
|---|---|---|
| `pyi_entry.py` | PyInstaller | Entry point built into the `cirdan` binary. Referenced by `../cirdan.spec`. |
| `install.sh` | curl \| sh | Downloads the matching release asset to `~/.local/bin`. POSIX (Linux glibc, macOS). |
| `install.ps1` | irm \| iex | Windows equivalent. |
| `homebrew/cirdan.rb` | Homebrew tap | Template formula; copy into a tap repo as `Formula/cirdan.rb`. |

Repo-root companions: [`../server.json`](../server.json) (official MCP registry
manifest) and [`../smithery.yaml`](../smithery.yaml) (Smithery listing).

## End-user install commands

```bash
# npm / npx (no Python)
npx -y @cirdanops/cli serve-mcp
npm install -g @cirdanops/cli

# curl installer
curl -LsSf https://raw.githubusercontent.com/adanb13/cirdan/main/packaging/install.sh | sh

# Windows
irm https://raw.githubusercontent.com/adanb13/cirdan/main/packaging/install.ps1 | iex

# Homebrew (once the tap is published)
brew install adanb13/tap/cirdan
```

## Discovery channels (one-time / per-release submissions)

These are external submissions, not code — track them as release checklist items:

- **Official MCP Registry** — publish `server.json` with the
  [`mcp-publisher`](https://github.com/modelcontextprotocol/registry) CLI. The
  `io.github.adanb13/*` namespace is verified via this GitHub repo. Bump the
  `version` fields to match the release.
- **Smithery** — connect the repo at <https://smithery.ai/new>; it reads
  `smithery.yaml`.
- **Directories** — submit/PR to `mcp.so`, PulseMCP, Glama, and
  `punkpeye/awesome-mcp-servers`.
- **Editor galleries** — VS Code MCP gallery and the Cursor MCP directory once
  the npx command is live.

## Per-release maintenance

1. The `version-guard` job asserts `pyproject.toml` == `cirdan/__init__.py` == tag.
2. npm versions are derived from the tag automatically.
3. For Homebrew: bump `version` + URLs in `homebrew/cirdan.rb` and fill the
   `sha256` values (`shasum -a 256 cirdan-*` over the release assets), then push
   to the tap. This can be automated as a follow-up.
4. Bump `version` in `server.json` (npm + pypi package entries) and re-publish to
   the MCP registry.
