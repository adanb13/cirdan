from cirdan.fingerprint.engine import fingerprint_environment, score
from cirdan.adapters.base import Signal
from tests.conftest import make_access


def test_score_combines_weights():
    detected = score(
        [
            Signal(system="docker", weight=0.5, evidence="socket"),
            Signal(system="docker", weight=0.8, evidence="daemon responded"),
        ]
    )
    assert detected[0].type == "docker"
    assert detected[0].confidence == 0.9
    assert detected[0].evidence == ["socket", "daemon responded"]


def test_fingerprint_compose_app(compose_app):
    access = make_access(docker_socket=True, docker_read=True)
    fp = fingerprint_environment(compose_app, access)
    assert fp.primary_runtime == "docker"
    assert fp.confidence_for("docker-compose") >= 0.7
    assert fp.primary_cloud is None


def test_fingerprint_k8s_aws_app(k8s_aws_app):
    access = make_access(kubeconfig=True, kubernetes_read=True, aws_read=True)
    fp = fingerprint_environment(k8s_aws_app, access)
    assert fp.primary_runtime == "kubernetes"
    assert fp.primary_cloud == "aws"
    assert "terraform" in fp.iac
    assert "helm" in fp.iac
    assert fp.confidence_for("kubernetes") >= 0.9
