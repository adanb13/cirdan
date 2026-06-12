"""Single-instance lock for the daemon.

flock-based: the lock is tied to the process's open file description, so it
vanishes with the process — a crashed or SIGKILLed daemon never leaves a stale
lock behind. The lock file's JSON payload (pid, started_at, root) is purely
diagnostic, for `cirdand status` and error messages.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cirdan.util import now_iso

try:
    import fcntl

    HAS_FCNTL = True
except ImportError:  # non-POSIX fallback handled below
    HAS_FCNTL = False


class DaemonAlreadyRunning(RuntimeError):
    def __init__(self, pid: int | None, started_at: str | None, path: Path):
        self.pid = pid
        self.started_at = started_at
        self.path = path
        detail = f"pid {pid}" if pid else "unknown pid"
        if started_at:
            detail += f", since {started_at}"
        super().__init__(f"cirdand already running for this project ({detail})")


def _read_holder(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) or {}
    except (OSError, json.JSONDecodeError):
        return {}


class DaemonLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if HAS_FCNTL:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                holder = _read_holder(self.path)
                raise DaemonAlreadyRunning(holder.get("pid"), holder.get("started_at"), self.path) from None
        else:
            # Best-effort pid-file semantics where flock is unavailable.
            holder = _read_holder(self.path)
            pid = holder.get("pid")
            if pid and _pid_alive(pid):
                os.close(fd)
                raise DaemonAlreadyRunning(pid, holder.get("started_at"), self.path)
        payload = json.dumps({"pid": os.getpid(), "started_at": now_iso(), "root": str(self.path.parent)})
        os.ftruncate(fd, 0)
        os.write(fd, payload.encode())
        os.fsync(fd)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if HAS_FCNTL:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass

    @property
    def held(self) -> bool:
        return self._fd is not None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def holder(path: Path) -> dict | None:
    """Who currently holds the lock at `path`, or None if it is free."""
    path = Path(path)
    if not path.exists():
        return None
    if HAS_FCNTL:
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None  # we could lock it → nobody holds it
        except OSError:
            return _read_holder(path) or {"pid": None}
        finally:
            os.close(fd)
    info = _read_holder(path)
    if info.get("pid") and _pid_alive(info["pid"]):
        return info
    return None
