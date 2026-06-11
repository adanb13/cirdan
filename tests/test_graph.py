from cirdan.graph import Confidence, Edge, GraphStore, Node, NodeType, Origin, Relation
from cirdan.graph.queries import GraphQueries


def make_store() -> GraphStore:
    store = GraphStore(":memory:")
    store.apply(
        nodes=[
            Node(id="service:api", type=NodeType.SERVICE.value, name="api"),
            Node(id="service:web", type=NodeType.SERVICE.value, name="web"),
            Node(
                id="database:postgres",
                type=NodeType.DATABASE.value,
                name="postgres",
                attrs={"published_ports": [5432]},
            ),
            Node(id="cache:redis", type=NodeType.CACHE.value, name="redis"),
        ],
        edges=[
            Edge(source="service:web", target="service:api", relation=Relation.CALLS),
            Edge(source="service:api", target="database:postgres", relation=Relation.CONNECTS_TO),
            Edge(source="service:api", target="cache:redis", relation=Relation.CONNECTS_TO),
        ],
    )
    return store


def test_upsert_merges_evidence_and_confidence():
    store = GraphStore(":memory:")
    store.upsert_node(
        Node(id="service:api", type="Service", name="api", confidence=Confidence.INFERRED, evidence=["a"])
    )
    merged = store.upsert_node(
        Node(
            id="service:api",
            type="Service",
            name="api",
            origin=Origin.LIVE,
            confidence=Confidence.EXTRACTED,
            evidence=["b", "a"],
        )
    )
    assert merged.confidence == Confidence.EXTRACTED
    assert merged.evidence == ["a", "b"]
    assert merged.origin == Origin.BOTH


def test_dependencies_and_dependents():
    queries = GraphQueries(make_store())
    deps = queries.dependencies("service:web")
    assert {d["node"].id for d in deps} == {"service:api", "database:postgres", "cache:redis"}
    dependents = queries.dependents("database:postgres")
    assert {d["node"].id for d in dependents} == {"service:api", "service:web"}


def test_shortest_path():
    queries = GraphQueries(make_store())
    path = queries.shortest_path("service:web", "database:postgres")
    assert [n.id for n in path] == ["service:web", "service:api", "database:postgres"]


def test_resolve_fuzzy():
    store = make_store()
    assert store.resolve("postgres").id == "database:postgres"
    assert store.resolve("service:api").id == "service:api"
    assert store.resolve("ap").id == "service:api"
    assert store.resolve("nope") is None


def test_sync_live_absent_tombstones():
    store = GraphStore(":memory:")
    store.upsert_node(
        Node(id="container:x", type="Container", name="x", origin=Origin.LIVE, source_adapter="docker")
    )
    store.upsert_node(
        Node(id="container:y", type="Container", name="y", origin=Origin.LIVE, source_adapter="docker")
    )
    gone = store.sync_live_absent("docker", {"container:y"})
    assert gone == ["container:x"]
    assert store.get_node("container:x").deleted
    assert not store.get_node("container:y").deleted


def test_public_entrypoints():
    queries = GraphQueries(make_store())
    entries = queries.public_entrypoints()
    assert [e["node"].id for e in entries] == ["database:postgres"]
    assert "published ports" in entries[0]["reasons"][0]
