"""Configuration loading. Cirdan works with zero config; cirdan.yaml refines it."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

CONFIG_FILENAMES = ("cirdan.yaml", "cirdan.yml", ".cirdan.yaml")

# Repo scanners — disabled in system scope, where only the live world matters.
STATIC_ADAPTER_NAMES = [
    "local-files", "docker-compose", "kubernetes-manifests", "terraform",
    "helm", "github-actions", "nginx", "sql-schema", "systemd-units",
]


class DaemonConfig(BaseModel):
    always_on: bool = True
    mcp: bool = False
    api: bool = False
    host: str = "127.0.0.1"
    port: int = 8090
    # Loop intervals in seconds.
    fingerprint_interval: float = 300.0
    graph_interval: float = 60.0
    incident_interval: float = 15.0
    export_interval: float = 60.0
    access_interval: float = 300.0


class ResponderConfig(BaseModel):
    """Push channel: act when an incident opens.

    Enabled by default: every qualifying incident gets an on-disk brief and any
    configured webhook/notify hooks fire. Spawning an agent additionally
    requires `command`, normally set by the `cirdan install` setup prompt.
    """

    enabled: bool = True
    # Placeholders: {brief_file} {incident_id} {title}
    command: str | None = None          # e.g. 'claude -p "Respond to the Cirdan incident brief at {brief_file}"'
    notify_command: str | None = None   # lighter hook, same placeholders
    webhook_url: str | None = None      # POST incident JSON on open/resolve
    severities: list[str] = Field(default_factory=lambda: ["high", "critical"])
    cooldown_seconds: float = 600.0     # per incident key
    timeout_seconds: float = 900.0
    max_concurrent: int = 1


class AdaptersConfig(BaseModel):
    auto_detect: bool = True
    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)


class TelemetryConfig(BaseModel):
    prometheus_url: str | None = None
    log_tail_lines: int = 200
    error_window_seconds: float = 600.0


class OutputConfig(BaseModel):
    dir: str = "cirdan-out"


class StorageConfig(BaseModel):
    graph: str = "sqlite"
    path: str | None = None  # defaults to <output.dir>/cirdan.db


class CirdanConfig(BaseModel):
    project: str | None = None
    environment: str | None = None
    root: str = "."
    output: OutputConfig = Field(default_factory=OutputConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    responder: ResponderConfig = Field(default_factory=ResponderConfig)

    @property
    def root_path(self) -> Path:
        return Path(self.root).resolve()

    @property
    def output_dir(self) -> Path:
        out = Path(self.output.dir)
        if not out.is_absolute():
            out = self.root_path / out
        return out

    @property
    def db_path(self) -> Path:
        if self.storage.path:
            p = Path(self.storage.path)
            return p if p.is_absolute() else self.root_path / p
        return self.output_dir / "cirdan.db"

    @classmethod
    def system(cls) -> "CirdanConfig":
        """Machine-level scope: watch everything the session can reach, no repo scanning.

        Graph, daemon, incidents, and artifacts live in ~/.cirdan; settings come
        from ~/.cirdan/cirdan.yaml when present. Live adapters are inherently
        machine/account-wide (docker ps, kubectl, aws), so this is the scope for
        "watch over ALL my infrastructure".
        """
        home = Path.home()
        base = home / ".cirdan"
        data: dict = {}
        cfg_file = base / "cirdan.yaml"
        if cfg_file.is_file():
            loaded = yaml.safe_load(cfg_file.read_text()) or {}
            if isinstance(loaded, dict):
                data = loaded
        data["root"] = str(home)
        data.setdefault("project", "system")
        data.setdefault("output", {})
        if not data["output"].get("dir"):
            data["output"]["dir"] = str(base)
        config = cls.model_validate(data)
        config.adapters.disabled = sorted(set(config.adapters.disabled) | set(STATIC_ADAPTER_NAMES))
        return config

    def ensure_output_dirs(self) -> Path:
        out = self.output_dir
        for sub in ("", "incidents", "views/generated", "views/saved", "evidence", "reports"):
            (out / sub).mkdir(parents=True, exist_ok=True)
        return out


def load_config(root: str | os.PathLike = ".", config_file: str | None = None) -> CirdanConfig:
    """Load cirdan.yaml from an explicit path or by discovery in the root directory."""
    root_path = Path(root).resolve()
    data: dict = {}
    path: Path | None = None
    if config_file:
        path = Path(config_file)
    else:
        for name in CONFIG_FILENAMES:
            candidate = root_path / name
            if candidate.is_file():
                path = candidate
                break
    if path and path.is_file():
        loaded = yaml.safe_load(path.read_text()) or {}
        if isinstance(loaded, dict):
            data = loaded
    data.setdefault("root", str(root_path))
    return CirdanConfig.model_validate(data)
