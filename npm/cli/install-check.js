#!/usr/bin/env node
"use strict";

// Non-fatal postinstall check. If no platform binary matched this OS/arch,
// print a friendly pointer to the Python install. This must never fail the
// install — the meta package invokes it as `node install-check.js || true`, and
// the launcher (bin/cirdan.js) emits the hard error at run time if needed.

const PLATFORM = `${process.platform}-${process.arch}`;
const PKG = `@cirdanops/cli-${PLATFORM}`;

try {
  require.resolve(`${PKG}/package.json`);
} catch (_) {
  process.stderr.write(
    `\ncirdan: no prebuilt binary for ${PLATFORM}; the CLI will not run from this npm install.\n` +
      `Use the Python distribution instead:  pip install cirdanops   (or pipx / uv tool install).\n\n`
  );
}
