"""PyInstaller entry point for the standalone ``cirdan`` binary.

Used by the npm / Homebrew / curl distributions. Builds the Typer ``app``, which
already exposes ``serve-mcp``, ``map``, ``setup``, ``install`` and the daemon
verbs (``status``/``stop``) — so a single frozen binary covers everything an
agent invokes. We deliberately ship only ``cirdan`` (not ``cirdand``); the pip
package still provides both.
"""

from cirdan.cli.main import app

if __name__ == "__main__":
    app()
