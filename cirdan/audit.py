"""Append-only audit log: everything Cirdan observes, generates, executes, verifies."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from cirdan.access.redaction import redact_obj
from cirdan.util import now_iso


class AuditWriter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, kind: str, summary: str, **details: object) -> dict:
        entry = {"ts": now_iso(), "kind": kind, "summary": summary}
        if details:
            entry["details"] = details
        entry = redact_obj(entry)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        return entry

    def tail(self, limit: int = 100) -> list[dict]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text().splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
