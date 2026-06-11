"""Canonical graph model. Every claim carries evidence and a confidence label."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cirdan.util import now_iso


class Confidence(str, Enum):
    EXTRACTED = "EXTRACTED"  # read directly from an authoritative source
    INFERRED = "INFERRED"    # derived from indirect evidence
    AMBIGUOUS = "AMBIGUOUS"  # conflicting or weak evidence
    UNKNOWN = "UNKNOWN"

    @property
    def rank(self) -> int:
        return {"EXTRACTED": 3, "INFERRED": 2, "AMBIGUOUS": 1, "UNKNOWN": 0}[self.value]


class Origin(str, Enum):
    STATIC = "static"    # declared in repo/config
    LIVE = "live"        # observed in the running system
    BOTH = "both"        # declared and observed
    DERIVED = "derived"  # created by Cirdan (incidents, actions, views)


class NodeType(str, Enum):
    ENVIRONMENT = "Environment"
    ACCESS_CONTEXT = "AccessContext"
    CLOUD_ACCOUNT = "CloudAccount"
    REGION = "Region"
    NETWORK = "Network"
    SUBNET = "Subnet"
    FIREWALL_RULE = "FirewallRule"
    LOAD_BALANCER = "LoadBalancer"
    DNS_RECORD = "DNSRecord"
    INGRESS = "Ingress"
    HOST = "Host"
    COMPUTE_NODE = "ComputeNode"
    CLUSTER = "Cluster"
    NAMESPACE = "Namespace"
    RUNTIME = "ContainerRuntime"
    SERVICE = "Service"
    WORKLOAD = "Workload"
    CONTAINER = "Container"
    POD = "Pod"
    DEPLOYMENT = "Deployment"
    REPLICA_SET = "ReplicaSet"
    STATEFUL_SET = "StatefulSet"
    DAEMON_SET = "DaemonSet"
    JOB = "Job"
    CRON_JOB = "CronJob"
    SYSTEMD_UNIT = "SystemdUnit"
    SERVERLESS_FUNCTION = "ServerlessFunction"
    DATABASE = "Database"
    QUEUE = "Queue"
    CACHE = "Cache"
    BUCKET = "Bucket"
    VOLUME = "Volume"
    SECRET_REF = "SecretReference"
    CONFIG = "Config"
    REPOSITORY = "Repository"
    COMMIT = "Commit"
    DEPLOY = "Deploy"
    PIPELINE = "Pipeline"
    ARTIFACT = "Artifact"
    LOG_STREAM = "LogStream"
    METRIC_SERIES = "MetricSeries"
    TRACE_SERVICE = "TraceService"
    EVENT_STREAM = "EventStream"
    ALERT = "Alert"
    INCIDENT = "Incident"
    ACTION = "Action"
    GENERATED_VIEW = "GeneratedView"
    OWNER = "Owner"
    TEAM = "Team"


class Relation(str, Enum):
    CONTAINS = "CONTAINS"
    RUNS_ON = "RUNS_ON"
    DEPLOYS = "DEPLOYS"
    DEFINES = "DEFINES"
    CREATES = "CREATES"
    OWNS = "OWNS"
    CALLS = "CALLS"
    CONNECTS_TO = "CONNECTS_TO"
    DEPENDS_ON = "DEPENDS_ON"
    READS_FROM = "READS_FROM"
    WRITES_TO = "WRITES_TO"
    EXPOSED_BY = "EXPOSED_BY"
    ROUTES_TO = "ROUTES_TO"
    DEFINED_IN = "DEFINED_IN"
    OBSERVED_IN = "OBSERVED_IN"
    TRIGGERED = "TRIGGERED"
    AFFECTS = "AFFECTS"
    FAILED_AFTER = "FAILED_AFTER"
    CORRELATES_WITH = "CORRELATES_WITH"
    GENERATED_FROM = "GENERATED_FROM"
    OPERATED_BY = "OPERATED_BY"


# Edge relations that express a runtime dependency, used by dependency queries.
DEPENDENCY_RELATIONS = {
    Relation.CONNECTS_TO,
    Relation.DEPENDS_ON,
    Relation.CALLS,
    Relation.READS_FROM,
    Relation.WRITES_TO,
}


class Node(BaseModel):
    id: str
    type: str
    name: str
    origin: Origin = Origin.STATIC
    source_adapter: str = ""
    confidence: Confidence = Confidence.EXTRACTED
    evidence: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)
    first_seen: str = Field(default_factory=now_iso)
    last_seen: str = Field(default_factory=now_iso)
    deleted: bool = False


class Edge(BaseModel):
    source: str
    target: str
    relation: Relation
    confidence: Confidence = Confidence.EXTRACTED
    evidence: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)
    first_seen: str = Field(default_factory=now_iso)
    last_seen: str = Field(default_factory=now_iso)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.relation.value)


class DiscoveryResult(BaseModel):
    adapter: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def merge(self, other: "DiscoveryResult") -> "DiscoveryResult":
        return DiscoveryResult(
            adapter=self.adapter or other.adapter,
            nodes=self.nodes + other.nodes,
            edges=self.edges + other.edges,
        )


def merge_confidence(a: Confidence, b: Confidence) -> Confidence:
    return a if a.rank >= b.rank else b


def merge_origin(a: Origin, b: Origin) -> Origin:
    if a == b:
        return a
    pair = {a, b}
    if pair <= {Origin.STATIC, Origin.LIVE, Origin.BOTH}:
        if pair == {Origin.STATIC, Origin.LIVE} or Origin.BOTH in pair:
            return Origin.BOTH
    return Origin.DERIVED if Origin.DERIVED in pair else Origin.BOTH
