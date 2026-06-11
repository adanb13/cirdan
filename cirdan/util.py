"""Shared helpers: subprocess execution with timeouts, time, and JSON utilities."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

DEFAULT_TIMEOUT = 5.0


@dataclass
class CmdResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    missing: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.missing


def run_cmd(argv: list[str], timeout: float = DEFAULT_TIMEOUT, input_text: str | None = None) -> CmdResult:
    """Run a command without a shell. Never raises; failures are reported in the result.

    Cirdan deliberately shells out to the CLIs already on PATH (docker, kubectl,
    aws, systemctl, ...) so it operates with exactly the access the current
    agent/session has.
    """
    if shutil.which(argv[0]) is None:
        return CmdResult(argv=argv, returncode=127, stdout="", stderr=f"{argv[0]}: not found", missing=True)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
        return CmdResult(argv=argv, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    except subprocess.TimeoutExpired:
        return CmdResult(argv=argv, returncode=-1, stdout="", stderr="timed out", timed_out=True)
    except OSError as exc:
        return CmdResult(argv=argv, returncode=-1, stdout="", stderr=str(exc))


def which(binary: str) -> bool:
    return shutil.which(binary) is not None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def parse_json_lines(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = parse_json(line)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def dump_json(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=False, default=str) + "\n"
