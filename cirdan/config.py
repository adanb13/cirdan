"""Configuration loading. Cirdan works with zero config; cirdan.yaml refines it."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

CONFIG_FILENAMES = ("cirdan.yaml", "cirdan.yml", ".cirdan.yaml")


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
