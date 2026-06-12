"""Once-a-day check whether PyPI has a newer cirdanops.

Strictly best-effort and quiet: any network or parse failure is cached and
silenced, the check is skipped entirely with CIRDAN_NO_UPDATE_CHECK=1, and the
caller only nudges when stderr is an interactive terminal.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import cirdan as cirdan_pkg

PYPI_URL = "https://pypi.org/pypi/cirdanops/json"
CACHE_TTL_SECONDS = 24 * 3600
DISABLE_ENV = "CIRDAN_NO_UPDATE_CHECK"


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "cirdan" / "update-check.json"


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    return _version_tuple(candidate) > _version_tuple(current)


def _fetch_latest() -> str | None:
    import httpx

    try:
        resp = httpx.get(PYPI_URL, timeout=2)
        if resp.status_code == 200:
            return resp.json()["info"]["version"]
    except Exception:
        pass
    return None


def check_for_update(current: str | None = None) -> str | None:
    """Return the newer PyPI version string, or None. Network at most once per day."""
    if os.environ.get(DISABLE_ENV):
        return None
    current = current or cirdan_pkg.__version__
    cache = _cache_path()
    latest: str | None = None
    try:
        data = json.loads(cache.read_text())
        if time.time() - float(data.get("checked_at", 0)) < CACHE_TTL_SECONDS:
            latest = data.get("latest")
        else:
            raise ValueError("stale")
    except (OSError, ValueError, json.JSONDecodeError):
        latest = _fetch_latest()
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
        except OSError:
            pass
    if latest and is_newer(latest, current):
        return latest
    return None


def upgrade_hint() -> str:
    """Best-guess upgrade command for how this copy was installed."""
    exe = os.path.realpath(os.sys.executable)
    if "pipx" in exe:
        return "pipx upgrade cirdanops"
    if "/uv/" in exe or "/.local/share/uv" in exe:
        return "uv tool upgrade cirdanops"
    return "pip install -U cirdanops"
