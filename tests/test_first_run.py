from pathlib import Path

import pytest

from cirdan.cli import main as cli_main


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def tty_stderr(monkeypatch):
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)


def _hints(capsys=None, recorder=None):
    return recorder


def test_first_run_prints_once(isolated_cache, tty_stderr, monkeypatch):
    printed = []
    monkeypatch.setattr(cli_main.status_console, "print", lambda *a, **k: printed.append(a))
    cli_main._first_run_hint("map")
    assert printed and "setup --system" in str(printed[0])
    assert (Path(isolated_cache) / "cirdan" / "first-run").exists()

    printed.clear()
    cli_main._first_run_hint("map")
    assert printed == []  # marker exists → silent


def test_no_hint_when_running_setup_itself(isolated_cache, tty_stderr, monkeypatch):
    printed = []
    monkeypatch.setattr(cli_main.status_console, "print", lambda *a, **k: printed.append(a))
    cli_main._first_run_hint("setup")
    assert printed == []
    # Marker still written so it never appears later either.
    assert (Path(isolated_cache) / "cirdan" / "first-run").exists()


def test_no_hint_without_tty(isolated_cache, monkeypatch):
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    printed = []
    monkeypatch.setattr(cli_main.status_console, "print", lambda *a, **k: printed.append(a))
    cli_main._first_run_hint("map")
    assert printed == []
