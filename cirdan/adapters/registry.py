"""Adapter registry: instantiates adapters and filters by availability and config."""

from __future__ import annotations

from cirdan.access.context import AccessContext
from cirdan.adapters.base import Adapter
from cirdan.config import CirdanConfig


def _adapter_classes() -> list[type[Adapter]]:
    # Imported lazily so optional-extra adapters degrade gracefully.
    from cirdan.adapters.docker_compose import DockerComposeAdapter
    from cirdan.adapters.github_actions import GitHubActionsAdapter
    from cirdan.adapters.helm import HelmAdapter
    from cirdan.adapters.k8s_manifests import K8sManifestsAdapter
    from cirdan.adapters.local_files import LocalFilesAdapter
    from cirdan.adapters.nginx import NginxAdapter
    from cirdan.adapters.sql_schema import SqlSchemaAdapter
    from cirdan.adapters.systemd_units import SystemdUnitsAdapter
    from cirdan.adapters.terraform import TerraformAdapter

    classes: list[type[Adapter]] = [
        LocalFilesAdapter,
        DockerComposeAdapter,
        K8sManifestsAdapter,
        TerraformAdapter,
        HelmAdapter,
        GitHubActionsAdapter,
        NginxAdapter,
        SqlSchemaAdapter,
        SystemdUnitsAdapter,
    ]
    try:
        from cirdan.adapters.docker import DockerAdapter
        from cirdan.adapters.kubernetes import KubernetesAdapter
        from cirdan.adapters.systemd import SystemdAdapter
        from cirdan.adapters.aws import AwsAdapter
        from cirdan.adapters.prometheus import PrometheusAdapter

        classes += [DockerAdapter, KubernetesAdapter, SystemdAdapter, AwsAdapter, PrometheusAdapter]
    except ImportError:
        pass
    return classes


def get_adapters(config: CirdanConfig, access: AccessContext, kind: str | None = None) -> list[Adapter]:
    adapters: list[Adapter] = []
    cfg = config.adapters
    for cls in _adapter_classes():
        if kind and cls.kind != kind:
            continue
        if cls.name in cfg.disabled:
            continue
        if cfg.enabled and not cfg.auto_detect and cls.name not in cfg.enabled:
            continue
        adapter = cls(config, access)
        try:
            if adapter.available():
                adapters.append(adapter)
        except Exception:
            continue
    return adapters


def get_adapter(config: CirdanConfig, access: AccessContext, name: str) -> Adapter | None:
    for adapter in get_adapters(config, access):
        if adapter.name == name:
            return adapter
    return None
