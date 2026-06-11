"""Shared classification and dependency-inference helpers used by several adapters."""

from __future__ import annotations

import re

from cirdan.graph.schema import NodeType

# Well-known backing components, matched against image names, hostnames, and service names.
COMPONENT_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, node type, id prefix)
    (r"postgres|pgbouncer|timescale", NodeType.DATABASE.value, "database"),
    (r"mysql|mariadb|percona", NodeType.DATABASE.value, "database"),
    (r"mongo", NodeType.DATABASE.value, "database"),
    (r"\brds\b", NodeType.DATABASE.value, "database"),
    (r"redis|valkey|keydb|memcache", NodeType.CACHE.value, "cache"),
    (r"rabbitmq|kafka|nats|activemq|\bsqs\b|pulsar", NodeType.QUEUE.value, "queue"),
    (r"elasticsearch|opensearch|clickhouse|cassandra|influxdb", NodeType.DATABASE.value, "database"),
    (r"minio|\bs3\b", NodeType.BUCKET.value, "bucket"),
    (r"nginx|traefik|haproxy|envoy|caddy", NodeType.LOAD_BALANCER.value, "loadbalancer"),
]

SCHEME_TYPES: dict[str, tuple[str, str]] = {
    "postgres": (NodeType.DATABASE.value, "database"),
    "postgresql": (NodeType.DATABASE.value, "database"),
    "mysql": (NodeType.DATABASE.value, "database"),
    "mariadb": (NodeType.DATABASE.value, "database"),
    "mongodb": (NodeType.DATABASE.value, "database"),
    "mongodb+srv": (NodeType.DATABASE.value, "database"),
    "redis": (NodeType.CACHE.value, "cache"),
    "rediss": (NodeType.CACHE.value, "cache"),
    "memcached": (NodeType.CACHE.value, "cache"),
    "amqp": (NodeType.QUEUE.value, "queue"),
    "amqps": (NodeType.QUEUE.value, "queue"),
    "kafka": (NodeType.QUEUE.value, "queue"),
    "nats": (NodeType.QUEUE.value, "queue"),
    "s3": (NodeType.BUCKET.value, "bucket"),
    "http": (NodeType.SERVICE.value, "service"),
    "https": (NodeType.SERVICE.value, "service"),
    "grpc": (NodeType.SERVICE.value, "service"),
}

_URL_RE = re.compile(r"\b([a-z][a-z0-9+.-]{1,20})://(?:[^@\s/]+@)?([a-zA-Z0-9_.-]+)(?::(\d+))?")
_HOST_KEY_RE = re.compile(r"(HOST|ADDR|ADDRESS|ENDPOINT|URL|URI|SERVER|BROKER)S?$", re.IGNORECASE)
_KEY_HINTS: list[tuple[str, tuple[str, str]]] = [
    ("REDIS", (NodeType.CACHE.value, "cache")),
    ("CACHE", (NodeType.CACHE.value, "cache")),
    ("DATABASE", (NodeType.DATABASE.value, "database")),
    ("POSTGRES", (NodeType.DATABASE.value, "database")),
    ("MYSQL", (NodeType.DATABASE.value, "database")),
    ("MONGO", (NodeType.DATABASE.value, "database")),
    ("DB", (NodeType.DATABASE.value, "database")),
    ("QUEUE", (NodeType.QUEUE.value, "queue")),
    ("BROKER", (NodeType.QUEUE.value, "queue")),
    ("KAFKA", (NodeType.QUEUE.value, "queue")),
    ("AMQP", (NodeType.QUEUE.value, "queue")),
    ("S3", (NodeType.BUCKET.value, "bucket")),
    ("BUCKET", (NodeType.BUCKET.value, "bucket")),
]

_GENERIC_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


def classify_component(name: str, image: str = "") -> tuple[str, str]:
    """Classify a service/host into (node_type, id_prefix) from its name or image."""
    haystack = f"{name} {image}".lower()
    for pattern, node_type, prefix in COMPONENT_PATTERNS:
        if re.search(pattern, haystack):
            return node_type, prefix
    return NodeType.SERVICE.value, "service"


def node_id(prefix: str, name: str) -> str:
    return f"{prefix}:{name}"


class ConnectionRef:
    """A dependency discovered in configuration (usually environment variables)."""

    def __init__(self, host: str, node_type: str, prefix: str, evidence: str, port: int | None = None):
        self.host = host
        self.node_type = node_type
        self.prefix = prefix
        self.evidence = evidence
        self.port = port

    @property
    def name(self) -> str:
        # First DNS label is the component name for cluster-local hostnames.
        return self.host.split(".")[0]


def infer_connections(env: dict[str, str], context: str) -> list[ConnectionRef]:
    """Infer outbound dependencies from environment-variable style key/value pairs."""
    refs: list[ConnectionRef] = []
    seen: set[str] = set()
    for key, raw in env.items():
        value = str(raw or "")
        for match in _URL_RE.finditer(value):
            scheme, host, port = match.group(1).lower(), match.group(2), match.group(3)
            if host.lower() in _GENERIC_HOSTS or host in seen:
                continue
            node_type, prefix = SCHEME_TYPES.get(scheme, (NodeType.SERVICE.value, "service"))
            seen.add(host)
            refs.append(
                ConnectionRef(
                    host=host,
                    node_type=node_type,
                    prefix=prefix,
                    evidence=f"{key} references {scheme}://{host} in {context}",
                    port=int(port) if port else None,
                )
            )
        # Bare hostnames under *_HOST style keys.
        if _HOST_KEY_RE.search(key) and value and "://" not in value:
            host = value.split(":")[0].strip()
            if not host or host.lower() in _GENERIC_HOSTS or host in seen:
                continue
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]*", host):
                continue
            node_type, prefix = NodeType.SERVICE.value, "service"
            for hint, mapping in _KEY_HINTS:
                if hint in key.upper():
                    node_type, prefix = mapping
                    break
            seen.add(host)
            refs.append(
                ConnectionRef(
                    host=host, node_type=node_type, prefix=prefix,
                    evidence=f"{key}={host} in {context}",
                )
            )
    return refs
