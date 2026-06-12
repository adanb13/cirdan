import json
import time

import pytest

from cirdan import update_check


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv(update_check.DISABLE_ENV, raising=False)


def test_version_comparison():
    assert update_check.is_newer("0.2.2", "0.2.1")
    assert update_check.is_newer("0.10.0", "0.9.9")
    assert not update_check.is_newer("0.2.1", "0.2.1")
    assert not update_check.is_newer("0.2.0", "0.2.1")
    assert update_check.is_newer("1.0.0rc1", "0.9.0")  # non-numeric suffixes degrade gracefully


def test_returns_newer_version(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch_latest", lambda: "9.9.9")
    assert update_check.check_for_update(current="0.2.1") == "9.9.9"


def test_silent_when_current(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch_latest", lambda: "0.2.1")
    assert update_check.check_for_update(current="0.2.1") is None


def test_cache_prevents_second_fetch(monkeypatch):
    calls = []

    def fetch():
        calls.append(1)
        return "9.9.9"

    monkeypatch.setattr(update_check, "_fetch_latest", fetch)
    assert update_check.check_for_update(current="0.2.1") == "9.9.9"
    assert update_check.check_for_update(current="0.2.1") == "9.9.9"  # served from cache
    assert len(calls) == 1


def test_stale_cache_refreshes(monkeypatch):
    cache = update_check._cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"checked_at": time.time() - 90000, "latest": "0.0.1"}))
    monkeypatch.setattr(update_check, "_fetch_latest", lambda: "9.9.9")
    assert update_check.check_for_update(current="0.2.1") == "9.9.9"


def test_env_opt_out(monkeypatch):
    monkeypatch.setenv(update_check.DISABLE_ENV, "1")
    monkeypatch.setattr(update_check, "_fetch_latest", lambda: "9.9.9")
    assert update_check.check_for_update(current="0.2.1") is None


def test_fetch_failure_is_silent(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch_latest", lambda: None)
    assert update_check.check_for_update(current="0.2.1") is None
