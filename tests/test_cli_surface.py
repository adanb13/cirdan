"""Regression guard: the CLI command surface can never silently lose commands again
(v0.4.0 shipped without install/setup/serve-mcp because nothing asserted registration)."""

import os

import pytest
from typer.testing import CliRunner

from cirdan.cli.main import app, daemon_app

EXPECTED_CIRDAN_COMMANDS = {
    "map", "query", "show", "incidents", "explain", "fingerprint", "access",
    "watch", "respond", "verify", "install", "setup", "serve-mcp",
    "status", "stop",
}
EXPECTED_CIRDAND_COMMANDS = {"serve", "status", "stop"}


def _names(typer_app):
    return {
        command.name or command.callback.__name__.replace("_", "-")
        for command in typer_app.registered_commands
    }


def test_cirdan_commands_registered():
    assert EXPECTED_CIRDAN_COMMANDS <= _names(app)


def test_cirdand_commands_registered():
    assert EXPECTED_CIRDAND_COMMANDS <= {
        c.name or c.callback.__name__.split("_", 1)[-1] for c in daemon_app.registered_commands
    }


@pytest.mark.parametrize("command", sorted(EXPECTED_CIRDAN_COMMANDS) + ["actions", "graph"])
def test_every_command_has_working_help(command, monkeypatch):
    if command == "graph":
        pytest.importorskip("cirdan.graph.contrib", reason="graph sub-app lands in iteration 1")
    monkeypatch.setenv("CIRDAN_NO_UPDATE_CHECK", "1")
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, f"`cirdan {command} --help` failed:\n{result.output}"
