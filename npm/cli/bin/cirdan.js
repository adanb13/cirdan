#!/usr/bin/env node
"use strict";

// Launcher for the Cirdan standalone binary.
//
// npm installs exactly one of the @cirdanops/cli-<platform> optionalDependencies
// — the one whose `os`/`cpu` match this host (the others fail their gate and are
// skipped). This shim resolves whichever landed and execs the bundled binary,
// forwarding argv and inheriting stdio so `cirdan serve-mcp` is a clean stdio
// JSON-RPC passthrough between the agent and the MCP server.

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");

const PLATFORM = `${process.platform}-${process.arch}`; // e.g. linux-x64, darwin-arm64, win32-x64
const PKG = `@cirdanops/cli-${PLATFORM}`;
const BIN = process.platform === "win32" ? "cirdan.exe" : "cirdan";

function resolveBinary() {
  try {
    // require.resolve finds the installed platform package regardless of hoisting.
    const pkgJsonPath = require.resolve(`${PKG}/package.json`);
    const binPath = pkgJsonPath.slice(0, -"package.json".length) + "bin/" + BIN;
    if (fs.existsSync(binPath)) return binPath;
  } catch (_) {
    // platform package not installed (unsupported platform) — fall through.
  }
  return null;
}

const bin = resolveBinary();
if (!bin) {
  process.stderr.write(
    `\ncirdan: no prebuilt binary for ${PLATFORM}.\n` +
      `Supported: darwin-arm64, linux-x64, linux-arm64, win32-x64 (glibc); Intel Macs use pip.\n` +
      `On Alpine/musl, FreeBSD, or other platforms, install via Python instead:\n` +
      `    pip install cirdanops        # or: pipx install cirdanops / uv tool install cirdanops\n` +
      `then run:  cirdan serve-mcp\n\n`
  );
  process.exit(1);
}

if (process.platform !== "win32") {
  try {
    fs.chmodSync(bin, 0o755); // npm usually preserves mode, but be safe on POSIX.
  } catch (_) {
    /* best effort */
  }
}

const result = spawnSync(bin, process.argv.slice(2), { stdio: "inherit" });
if (result.error) {
  process.stderr.write(`cirdan: failed to launch binary: ${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
