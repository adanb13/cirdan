from cirdan.graph.builder import GraphBuilder
from cirdan.graph.queries import GraphQueries
from cirdan.graph.schema import Confidence, Origin, Relation
from cirdan.graph.store import GraphStore


def build(config, access) -> GraphStore:
    store = GraphStore(":memory:")
    GraphBuilder(config, access, store).run_static()
    return store


def test_compose_discovery(compose_app, access):
    store = build(compose_app, access)
    ids = {n.id for n in store.all_nodes()}
    assert {"service:api", "database:postgres", "cache:redis", "loadbalancer:web"} <= ids

    edges = {(e.source, e.target, e.relation) for e in store.all_edges()}
    assert ("service:api", "database:postgres", Relation.DEPENDS_ON) in edges
    # CONNECTS_TO inferred from DATABASE_URL and REDIS_HOST
    assert ("service:api", "database:postgres", Relation.CONNECTS_TO) in edges
    assert ("service:api", "cache:redis", Relation.CONNECTS_TO) in edges
    # External payments service inferred from PAYMENTS_URL
    payments = store.resolve("payments")
    assert payments is not None
    assert payments.confidence == Confidence.INFERRED

    web = store.get_node("loadbalancer:web")
    assert web.attrs.get("public") is True
    assert web.attrs.get("published_ports") == ["80"]


def test_compose_pipeline_and_schema(compose_app, access):
    store = build(compose_app, access)
    pipelines = store.find_nodes(type="Pipeline")
    assert len(pipelines) == 1
    assert pipelines[0].attrs["deploys"] is True  # docker push hint
    schemas = store.find_nodes(type="Config")
    assert any("users" in (n.attrs.get("tables") or []) for n in schemas)


def test_k8s_manifest_discovery(k8s_aws_app, access):
    store = build(k8s_aws_app, access)
    checkout = store.get_node("service:checkout-api")
    assert checkout is not None
    assert checkout.attrs["replicas"] == 3
    assert checkout.attrs["service_type"] == "LoadBalancer"
    assert checkout.origin == Origin.STATIC

    edges = {(e.source, e.target, e.relation) for e in store.all_edges()}
    assert ("namespace:production", "service:checkout-api", Relation.CONTAINS) in edges
    assert ("ingress:checkout-ingress", "service:checkout-api", Relation.ROUTES_TO) in edges

    # RDS hostname from env var becomes an inferred external database.
    rds = store.resolve("postgres-prod")
    assert rds is not None
    assert rds.type == "Database"
    assert rds.confidence == Confidence.INFERRED
    assert ("service:checkout-api", rds.id, Relation.CONNECTS_TO) in edges


def test_terraform_and_helm_discovery(k8s_aws_app, access):
    store = build(k8s_aws_app, access)
    ids = {n.id for n in store.all_nodes()}
    assert "tf:aws_db_instance.postgres_prod" in ids
    assert "tf:aws_sqs_queue.payments_jobs" in ids
    assert "tf:aws_eks_cluster.prod" in ids
    assert store.get_node("tf:aws_db_instance.postgres_prod").type == "Database"

    myapp = store.resolve("myapp")
    assert myapp is not None and myapp.attrs.get("helm_chart")
    edges = {(e.source, e.target, e.relation) for e in store.all_edges()}
    assert ("service:myapp", "database:postgresql", Relation.DEPENDS_ON) in edges


def test_public_entrypoints_from_static(k8s_aws_app, access):
    queries = GraphQueries(build(k8s_aws_app, access))
    entries = {e["node"].id for e in queries.public_entrypoints()}
    assert "ingress:checkout-ingress" in entries
    assert "service:checkout-api" in entries
