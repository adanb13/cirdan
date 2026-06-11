"""Normalized event model and storage. Raw logs stay outside the graph; only
patterns (clusters, incidents) become graph nodes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from cirdan.access.redaction import redact_text
from cirdan.graph.store import GraphStore
from cirdan.util import now_iso

_ERROR_RE = re.compile(r"\b(error|err|fatal|panic|exception|traceback|refused|timeout|timed out|denied|failed|failure|unavailable|oom)\b", re.IGNORECASE)
_WARN_RE = re.compile(r"\b(warn|warning|retry|retrying|slow|deprecated)\b", re.IGNORECASE)


class Event(BaseModel):
    ts: str = Field(default_factory=now_iso)
    source_type: str = "log"          # log | event | state
    provider: str = ""                # docker | kubernetes | systemd | ...
    severity: str = "info"            # info | warning | error
    resource: str = ""                # node id or provider resource name
    service: str = ""                 # logical service name when known
    message: str = ""
    error_family: str = ""            # cluster key for error-shaped messages
    data: dict = Field(default_factory=dict)


def severity_of(message: str) -> str:
    if _ERROR_RE.search(message):
        return "error"
    if _WARN_RE.search(message):
        return "warning"
    return "info"


def log_line_to_event(line: str, provider: str, resource: str, service: str = "") -> Event:
    from cirdan.telemetry.clusters import template_key

    message = redact_text(line.strip())[:2000]
    severity = severity_of(message)
    return Event(
        source_type="log",
        provider=provider,
        severity=severity,
        resource=resource,
        service=service,
        message=message,
        error_family=template_key(message) if severity != "info" else "",
    )


def docker_event_to_event(raw: dict) -> Event:
    action = raw.get("action") or ""
    name = raw.get("resource") or ""
    bad = action in {"die", "oom", "kill", "destroy", "health_status: unhealthy"}
    return Event(
        source_type="event",
        provider="docker",
        severity="warning" if bad else "info",
        resource=f"container:{name}" if name else "",
        service=(raw.get("raw", {}).get("Actor", {}).get("Attributes", {}) or {}).get(
            "com.docker.compose.service", ""
        ),
        message=f"docker {raw.get('type', '')} {action} {name}".strip(),
        data={"action": action},
    )


def k8s_event_to_event(raw: dict) -> Event:
    return Event(
        ts=raw.get("timestamp") or now_iso(),
        source_type="event",
        provider="kubernetes",
        severity="warning" if raw.get("type") == "Warning" else "info",
        resource=raw.get("resource", ""),
        service="",
        message=redact_text(f"{raw.get('reason', '')}: {raw.get('message', '')}")[:2000],
        data={"namespace": raw.get("namespace"), "count": raw.get("count", 1)},
    )


class EventStore:
    """Events live in the same SQLite file as the graph."""

    def __init__(self, store: GraphStore):
        self.store = store

    def add(self, event: Event) -> None:
        with self.store.lock:
            self.store.conn.execute(
                """INSERT INTO events (ts, source_type, provider, severity, resource, service,
                                       message, error_family, data)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (event.ts, event.source_type, event.provider, event.severity, event.resource,
                 event.service, event.message, event.error_family, json.dumps(event.data, default=str)),
            )
            self.store.conn.commit()

    def add_many(self, events: list[Event]) -> None:
        for event in events:
            self.add(event)

    def recent(
        self,
        window_seconds: float = 3600,
        severity: tuple[str, ...] = (),
        service: str | None = None,
        limit: int = 2000,
    ) -> list[Event]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        clauses, params = ["ts >= ?"], [cutoff]
        if severity:
            clauses.append(f"severity IN ({','.join('?' * len(severity))})")
            params += list(severity)
        if service:
            clauses.append("(service = ? OR resource LIKE ?)")
            params += [service, f"%{service}%"]
        sql = (
            "SELECT ts, source_type, provider, severity, resource, service, message, error_family, data "
            f"FROM events WHERE {' AND '.join(clauses)} ORDER BY ts DESC LIMIT ?"
        )
        params.append(limit)
        with self.store.lock:
            rows = self.store.conn.execute(sql, params).fetchall()
        return [
            Event(
                ts=r["ts"], source_type=r["source_type"], provider=r["provider"],
                severity=r["severity"], resource=r["resource"], service=r["service"],
                message=r["message"], error_family=r["error_family"],
                data=json.loads(r["data"] or "{}"),
            )
            for r in rows
        ]

    def prune(self, keep: int = 50000) -> None:
        with self.store.lock:
            self.store.conn.execute(
                "DELETE FROM events WHERE id < (SELECT COALESCE(MIN(id),0) FROM "
                "(SELECT id FROM events ORDER BY id DESC LIMIT ?))",
                (keep,),
            )
            self.store.conn.commit()
