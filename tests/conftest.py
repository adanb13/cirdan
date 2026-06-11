from pathlib import Path

import pytest

from cirdan.access.context import AccessContext
from cirdan.config import CirdanConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def make_access(**caps: bool) -> AccessContext:
    defaults = {"file_read": True, "file_write": True, "shell": True}
    defaults.update(caps)
    return AccessContext(capabilities=defaults, source={"agent": "test", "workspace": "/tmp", "user": "test"})


def make_config(root: Path) -> CirdanConfig:
    return CirdanConfig(root=str(root))


@pytest.fixture
def compose_app(fixtures_dir) -> CirdanConfig:
    return make_config(fixtures_dir / "repos" / "compose-app")


@pytest.fixture
def k8s_aws_app(fixtures_dir) -> CirdanConfig:
    return make_config(fixtures_dir / "repos" / "k8s-aws-app")


@pytest.fixture
def access() -> AccessContext:
    return make_access()
