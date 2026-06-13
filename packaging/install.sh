#!/bin/sh
# Cirdan installer — downloads the zero-Python standalone binary for this
# platform from GitHub Releases and drops it on your PATH.
#
#   curl -LsSf https://raw.githubusercontent.com/adanb13/cirdan/main/packaging/install.sh | sh
#
# Env overrides:
#   CIRDAN_VERSION   release to install (e.g. 0.7.0); default: latest
#   CIRDAN_BIN_DIR   install dir; default: $HOME/.local/bin
set -eu

REPO="adanb13/cirdan"
BIN_DIR="${CIRDAN_BIN_DIR:-$HOME/.local/bin}"
VERSION="${CIRDAN_VERSION:-latest}"

os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
  Linux) os="linux" ;;
  Darwin) os="darwin" ;;
  *) echo "cirdan: unsupported OS '$os' — install via Python: pip install cirdanops" >&2; exit 1 ;;
esac

case "$arch" in
  x86_64 | amd64) arch="x64" ;;
  arm64 | aarch64) arch="arm64" ;;
  *) echo "cirdan: unsupported arch '$arch' — install via Python: pip install cirdanops" >&2; exit 1 ;;
esac

# The prebuilt Linux binary is glibc-only; Alpine/musl must use the Python build.
if [ "$os" = "linux" ] && [ -f /etc/alpine-release ]; then
  echo "cirdan: Alpine/musl detected — the prebuilt binary is glibc-only." >&2
  echo "        Install via Python instead: pip install cirdanops" >&2
  exit 1
fi

target="${os}-${arch}"
if [ "$VERSION" = "latest" ]; then
  url="https://github.com/${REPO}/releases/latest/download/cirdan-${target}"
else
  url="https://github.com/${REPO}/releases/download/v${VERSION}/cirdan-${target}"
fi

echo "cirdan: downloading ${target} (${VERSION}) from ${url}"
mkdir -p "$BIN_DIR"
tmp="$(mktemp)"
if command -v curl >/dev/null 2>&1; then
  curl -fSL "$url" -o "$tmp"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$tmp" "$url"
else
  echo "cirdan: need curl or wget to download" >&2
  exit 1
fi

chmod +x "$tmp"
mv "$tmp" "$BIN_DIR/cirdan"
echo "cirdan: installed -> $BIN_DIR/cirdan"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "cirdan: add $BIN_DIR to your PATH to use the 'cirdan' command." ;;
esac

"$BIN_DIR/cirdan" --version || true
echo "cirdan: try 'cirdan serve-mcp' to start the MCP server."
