"""Static adapter: Terraform/OpenTofu files → declared cloud resources and providers."""

from __future__ import annotations

from pathlib import Path

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import Confidence, DiscoveryResult, Node, NodeType, Origin

try:
    import hcl2  # type: ignore

    HAS_HCL2 = True
except ImportError:  # graceful degradation without the [terraform] extra
    HAS_HCL2 = False

RESOURCE_TYPE_MAP = {
    "aws_db_instance": NodeType.DATABASE.value,
    "aws_rds_cluster": NodeType.DATABASE.value,
    "aws_dynamodb_table": NodeType.DATABASE.value,
    "aws_elasticache_cluster": NodeType.CACHE.value,
    "aws_elasticache_replication_group": NodeType.CACHE.value,
    "aws_sqs_queue": NodeType.QUEUE.value,
    "aws_sns_topic": NodeType.QUEUE.value,
    "aws_mq_broker": NodeType.QUEUE.value,
    "aws_s3_bucket": NodeType.BUCKET.value,
    "aws_instance": NodeType.COMPUTE_NODE.value,
    "aws_autoscaling_group": NodeType.COMPUTE_NODE.value,
    "aws_eks_cluster": NodeType.CLUSTER.value,
    "aws_ecs_cluster": NodeType.CLUSTER.value,
    "aws_ecs_service": NodeType.SERVICE.value,
    "aws_lb": NodeType.LOAD_BALANCER.value,
    "aws_alb": NodeType.LOAD_BALANCER.value,
    "aws_elb": NodeType.LOAD_BALANCER.value,
    "aws_route53_record": NodeType.DNS_RECORD.value,
    "aws_vpc": NodeType.NETWORK.value,
    "aws_subnet": NodeType.SUBNET.value,
    "aws_security_group": NodeType.FIREWALL_RULE.value,
    "aws_lambda_function": NodeType.SERVERLESS_FUNCTION.value,
    "google_sql_database_instance": NodeType.DATABASE.value,
    "google_compute_instance": NodeType.COMPUTE_NODE.value,
    "google_container_cluster": NodeType.CLUSTER.value,
    "google_storage_bucket": NodeType.BUCKET.value,
    "azurerm_postgresql_server": NodeType.DATABASE.value,
    "azurerm_mysql_server": NodeType.DATABASE.value,
    "azurerm_kubernetes_cluster": NodeType.CLUSTER.value,
    "azurerm_storage_account": NodeType.BUCKET.value,
    "azurerm_virtual_machine": NodeType.COMPUTE_NODE.value,
}

PROVIDER_SYSTEMS = {"aws": "aws", "google": "gcp", "azurerm": "azure", "kubernetes": "kubernetes", "helm": "helm"}


def _unquote(value: object) -> object:
    """Some python-hcl2 versions keep literal quotes around keys and strings."""
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _clean(obj: object) -> object:
    if isinstance(obj, dict):
        return {
            _unquote(k): _clean(v)
            for k, v in obj.items()
            if k != "__is_block__"
        }
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return _unquote(obj)


class TerraformAdapter(Adapter):
    name = "terraform"
    kind = "static"

    def available(self) -> bool:
        return self.access.can("file_read")

    def _tf_files(self) -> list[Path]:
        return list(self.walk_files(".tf"))

    def fingerprint(self) -> list[Signal]:
        files = self._tf_files()
        signals = []
        if files:
            system = "opentofu" if any(p.name == ".tofu-version" for p in self.walk_files(names=(".tofu-version",))) else "terraform"
            signals.append(
                Signal(system=system, weight=0.7, evidence=f"{len(files)} .tf files (e.g. {self.rel(files[0])})")
            )
            for _, parsed in self._parse_all(files):
                for provider in parsed.get("provider", []) or []:
                    for pname in provider:
                        if pname in PROVIDER_SYSTEMS:
                            signals.append(
                                Signal(
                                    system=PROVIDER_SYSTEMS[pname],
                                    weight=0.35,
                                    evidence=f"Terraform provider '{pname}' configured",
                                )
                            )
        if any(self.walk_files(names=("terraform.tfstate",))):
            signals.append(Signal(system="terraform", weight=0.5, evidence="terraform.tfstate present"))
        return signals

    def _parse_all(self, files: list[Path]) -> list[tuple[str, dict]]:
        if not HAS_HCL2:
            return []
        parsed = []
        for path in files:
            try:
                with path.open() as fh:
                    parsed.append((self.rel(path), _clean(hcl2.load(fh))))
            except Exception:
                continue
        return parsed

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        files = self._tf_files()
        if not files:
            return result
        if not HAS_HCL2:
            # Without the parser we still record that IaC exists.
            result.nodes.append(
                Node(
                    id="config:terraform",
                    type=NodeType.CONFIG.value,
                    name="terraform",
                    origin=Origin.STATIC,
                    source_adapter=self.name,
                    confidence=Confidence.AMBIGUOUS,
                    evidence=[f"{len(files)} .tf files found; install cirdanops[terraform] to parse them"],
                )
            )
            return result
        for rel, parsed in self._parse_all(files):
            for block in parsed.get("resource", []) or []:
                for rtype, instances in block.items():
                    if not isinstance(instances, dict):
                        continue
                    for rname, body in instances.items():
                        node_type = RESOURCE_TYPE_MAP.get(rtype, "CloudResource")
                        attrs = {"terraform_type": rtype, "terraform_file": rel}
                        if isinstance(body, dict):
                            for key in ("engine", "instance_class", "instance_type", "node_type"):
                                if key in body and isinstance(body[key], (str, int)):
                                    attrs[key] = body[key]
                        result.nodes.append(
                            Node(
                                id=f"tf:{rtype}.{rname}",
                                type=node_type,
                                name=rname,
                                origin=Origin.STATIC,
                                source_adapter=self.name,
                                confidence=Confidence.EXTRACTED,
                                evidence=[f"resource \"{rtype}\" \"{rname}\" in {rel}"],
                                attrs=attrs,
                            )
                        )
        return result
