"""Live adapter: read-only AWS discovery via the aws CLI and credentials already present.

Every call is best-effort: a missing permission simply means that surface
stays invisible, mirroring exactly what the current session could see by hand.
"""

from __future__ import annotations

from cirdan.adapters.base import Adapter, Signal
from cirdan.graph.schema import Confidence, DiscoveryResult, Edge, Node, NodeType, Origin, Relation
from cirdan.util import parse_json, run_cmd

AWS_TIMEOUT = 15


def _aws_json(args: list[str]) -> dict | list | None:
    res = run_cmd(["aws", *args, "--output", "json"], timeout=AWS_TIMEOUT)
    return parse_json(res.stdout) if res.ok else None


class AwsAdapter(Adapter):
    name = "aws"
    kind = "live"

    def available(self) -> bool:
        return self.access.can("aws_read")

    def fingerprint(self) -> list[Signal]:
        if self.access.details.get("aws_account"):
            return [Signal(system="aws", weight=0.5,
                           evidence=f"AWS account {self.access.details['aws_account']} reachable")]
        return []

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult(adapter=self.name)
        account = self.access.details.get("aws_account", "unknown")
        account_id = f"aws-account:{account}"
        result.nodes.append(
            Node(id=account_id, type=NodeType.CLOUD_ACCOUNT.value, name=f"aws {account}",
                 origin=Origin.LIVE, source_adapter=self.name,
                 evidence=["aws sts get-caller-identity"],
                 attrs={"provider": "aws", "account": account})
        )

        def contain(nid: str, evidence: str) -> None:
            result.edges.append(
                Edge(source=account_id, target=nid, relation=Relation.CONTAINS,
                     confidence=Confidence.EXTRACTED, evidence=[evidence])
            )

        rds = _aws_json(["rds", "describe-db-instances"])
        for db in (rds or {}).get("DBInstances", []) if isinstance(rds, dict) else []:
            ident = db.get("DBInstanceIdentifier", "unknown")
            nid = f"database:{ident}"
            result.nodes.append(
                Node(id=nid, type=NodeType.DATABASE.value, name=ident,
                     origin=Origin.LIVE, source_adapter=self.name,
                     evidence=["aws rds describe-db-instances"],
                     attrs={"engine": db.get("Engine"), "state": db.get("DBInstanceStatus"),
                            "endpoint": (db.get("Endpoint") or {}).get("Address"),
                            "instance_class": db.get("DBInstanceClass"), "provider": "aws"})
            )
            contain(nid, "RDS instance in account")

        lbs = _aws_json(["elbv2", "describe-load-balancers"])
        for lb in (lbs or {}).get("LoadBalancers", []) if isinstance(lbs, dict) else []:
            name = lb.get("LoadBalancerName", "unknown")
            nid = f"loadbalancer:{name}"
            result.nodes.append(
                Node(id=nid, type=NodeType.LOAD_BALANCER.value, name=name,
                     origin=Origin.LIVE, source_adapter=self.name,
                     evidence=["aws elbv2 describe-load-balancers"],
                     attrs={"dns_name": lb.get("DNSName"), "state": (lb.get("State") or {}).get("Code"),
                            "public": lb.get("Scheme") == "internet-facing", "provider": "aws"})
            )
            contain(nid, "load balancer in account")

        eks = _aws_json(["eks", "list-clusters"])
        for name in (eks or {}).get("clusters", []) if isinstance(eks, dict) else []:
            nid = f"cluster:{name}"
            result.nodes.append(
                Node(id=nid, type=NodeType.CLUSTER.value, name=name,
                     origin=Origin.LIVE, source_adapter=self.name,
                     evidence=["aws eks list-clusters"], attrs={"provider": "aws", "kind": "eks"})
            )
            contain(nid, "EKS cluster in account")

        sqs = _aws_json(["sqs", "list-queues"])
        for url in (sqs or {}).get("QueueUrls", []) if isinstance(sqs, dict) else []:
            name = url.rstrip("/").rsplit("/", 1)[-1]
            nid = f"queue:{name}"
            result.nodes.append(
                Node(id=nid, type=NodeType.QUEUE.value, name=name,
                     origin=Origin.LIVE, source_adapter=self.name,
                     evidence=["aws sqs list-queues"], attrs={"provider": "aws"})
            )
            contain(nid, "SQS queue in account")

        ec2 = _aws_json(["ec2", "describe-instances", "--max-results", "100"])
        for reservation in (ec2 or {}).get("Reservations", []) if isinstance(ec2, dict) else []:
            for inst in reservation.get("Instances", []):
                iid = inst.get("InstanceId", "unknown")
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", []) or []}
                name = tags.get("Name", iid)
                nid = f"compute:{iid}"
                result.nodes.append(
                    Node(id=nid, type=NodeType.COMPUTE_NODE.value, name=name,
                         origin=Origin.LIVE, source_adapter=self.name,
                         evidence=["aws ec2 describe-instances"],
                         attrs={"instance_id": iid, "state": (inst.get("State") or {}).get("Name"),
                                "instance_type": inst.get("InstanceType"), "provider": "aws"})
                )
                contain(nid, "EC2 instance in account")

        s3 = _aws_json(["s3api", "list-buckets"])
        for bucket in (s3 or {}).get("Buckets", []) if isinstance(s3, dict) else []:
            name = bucket.get("Name", "unknown")
            nid = f"bucket:{name}"
            result.nodes.append(
                Node(id=nid, type=NodeType.BUCKET.value, name=name,
                     origin=Origin.LIVE, source_adapter=self.name,
                     evidence=["aws s3api list-buckets"], attrs={"provider": "aws"})
            )
            contain(nid, "S3 bucket in account")
        return result

    def collect_logs(self, scope: str, lines: int = 200) -> list[str]:
        group = scope.split(":", 1)[-1]
        res = run_cmd(
            ["aws", "logs", "tail", group, "--format", "short", "--since", "1h"], timeout=AWS_TIMEOUT
        )
        return res.stdout.splitlines()[-lines:] if res.ok else []

    def current_state(self, scope: str) -> dict:
        name = scope.split(":", 1)[-1]
        if scope.startswith("database:"):
            data = _aws_json(["rds", "describe-db-instances", "--db-instance-identifier", name])
            instances = (data or {}).get("DBInstances", []) if isinstance(data, dict) else []
            if instances:
                return {"state": instances[0].get("DBInstanceStatus"), "engine": instances[0].get("Engine")}
        return {}
