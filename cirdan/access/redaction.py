"""Scrub secret-shaped values before anything is written to artifacts or logs."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

SECRET_KEY_RE = re.compile(
    r"(secret|token|password|passwd|credential|api[_-]?key|access[_-]?key|private[_-]?key|auth)",
    re.IGNORECASE,
)

_PATTERNS = [
    # user:password@ in URLs
    re.compile(r"(?<=://)([^/\s:@]+):([^/\s@]+)(?=@)"),
    # AWS access key ids and session-ish tokens
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    # Bearer tokens
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    # key=value pairs where the key looks secret
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY)[A-Z0-9_]*)\s*[=:]\s*([^\s,;\"']+)"
    ),
    # PEM blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact_text(text: str) -> str:
    if not text:
        return text
    out = text
    out = _PATTERNS[0].sub(REDACTED, out)
    out = _PATTERNS[1].sub(REDACTED, out)
    out = _PATTERNS[2].sub(f"Bearer {REDACTED}", out)
    out = _PATTERNS[3].sub(lambda m: f"{m.group(1)}={REDACTED}", out)
    out = _PATTERNS[4].sub(REDACTED, out)
    return out


def redact_obj(obj: object) -> object:
    """Recursively redact strings; values under secret-shaped keys are dropped entirely."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if isinstance(key, str) and SECRET_KEY_RE.search(key) and isinstance(value, str) and value:
                out[key] = REDACTED
            else:
                out[key] = redact_obj(value)
        return out
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
