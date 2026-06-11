"""Error clustering by message templating: similar messages share a cluster key."""

from __future__ import annotations

import hashlib
import re

_SUBS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{12,64}\b", re.I), "<hex>"),
    (re.compile(r"\b\d+(\.\d+)*\b"), "<n>"),
    (re.compile(r"\"[^\"]*\""), "<str>"),
    (re.compile(r"'[^']*'"), "<str>"),
    (re.compile(r"\s+"), " "),
]


def template(message: str) -> str:
    out = message.strip().lower()
    for pattern, repl in _SUBS:
        out = pattern.sub(repl, out)
    return out[:300]


def template_key(message: str) -> str:
    return hashlib.sha1(template(message).encode()).hexdigest()[:10]


def cluster_events(events: list) -> list[dict]:
    """Group error/warning events by template. Returns clusters sorted by count."""
    clusters: dict[str, dict] = {}
    for event in events:
        if event.severity == "info":
            continue
        key = event.error_family or template_key(event.message)
        cluster = clusters.setdefault(
            key,
            {
                "key": key,
                "template": template(event.message),
                "count": 0,
                "severity": event.severity,
                "first_ts": event.ts,
                "last_ts": event.ts,
                "sample": event.message,
                "services": set(),
                "resources": set(),
                "providers": set(),
            },
        )
        cluster["count"] += event.data.get("count", 1) if isinstance(event.data, dict) else 1
        cluster["first_ts"] = min(cluster["first_ts"], event.ts)
        cluster["last_ts"] = max(cluster["last_ts"], event.ts)
        if event.severity == "error":
            cluster["severity"] = "error"
        if event.service:
            cluster["services"].add(event.service)
        if event.resource:
            cluster["resources"].add(event.resource)
        if event.provider:
            cluster["providers"].add(event.provider)
    out = []
    for cluster in clusters.values():
        cluster["services"] = sorted(cluster["services"])
        cluster["resources"] = sorted(cluster["resources"])
        cluster["providers"] = sorted(cluster["providers"])
        out.append(cluster)
    out.sort(key=lambda c: c["count"], reverse=True)
    return out
