"""Access-context detection.

Cirdan is not a permission manager: this module is a mirror of what the
current agent/session/process can already do. Every probe has a hard timeout
so detection never hangs in restricted sandboxes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from cirdan.config import CirdanConfig
from cirdan.util import now_iso, run_cmd, which


class AccessContext(BaseModel):
    detected_at: str = Field(default_factory=now_iso)
    mode: str = "inherited-agent-access"
    source: dict = Field(default_factory=dict)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)

    def can(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability))


def _detect_agent() -> str:
    env = os.environ
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude-code"
    if env.get("CODEX_HOME") or env.get("CODEX_SANDBOX"):
        return "codex"
    if env.get("CURSOR_TRACE_ID") or env.get("CURSOR_SESSION_ID"):
        return "cursor"
    if env.get("GEMINI_CLI"):
        return "gemini-cli"
    return "shell"


def _probe_files(root: Path) -> tuple[bool, bool]:
    readable = os.access(root, os.R_OK)
    writable = False
    if os.access(root, os.W_OK):
        probe = root / ".cirdan-write-probe"
        try:
            probe.write_text("")
            probe.unlink()
            writable = True
        except OSError:
            writable = False
    return readable, writable


def _probe_docker(caps: dict, details: dict) -> None:
    sock = Path("/var/run/docker.sock")
    caps["docker_socket"] = sock.exists() and stat.S_ISSOCK(sock.stat().st_mode) and os.access(sock, os.R_OK | os.W_OK)
    caps["docker_cli"] = which("docker")
    caps["docker_read"] = False
    if caps["docker_cli"]:
        res = run_cmd(["docker", "version", "--format", "{{.Server.Version}}"], timeout=4)
        caps["docker_read"] = res.ok
        if res.ok:
            details["docker_server_version"] = res.stdout.strip()
    # With Docker there is no finer-grained authz: daemon access implies write.
    caps["docker_write"] = caps["docker_read"]


def _probe_kubernetes(caps: dict, details: dict) -> None:
    kubeconfig = os.environ.get("KUBECONFIG")
    has_kubeconfig = bool(kubeconfig and Path(kubeconfig).is_file()) or (Path.home() / ".kube" / "config").is_file()
    in_cluster = bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
    caps["kubeconfig"] = has_kubeconfig
    caps["kubernetes_in_cluster"] = in_cluster
    caps["kubectl_cli"] = which("kubectl")
    caps["kubernetes_read"] = False
    caps["kubernetes_write"] = False
    if not caps["kubectl_cli"] or not (has_kubeconfig or in_cluster):
        return
    res = run_cmd(["kubectl", "get", "namespaces", "-o", "name", "--request-timeout=3s"], timeout=6)
    if res.ok:
        caps["kubernetes_read"] = True
        namespaces = [line.split("/", 1)[-1] for line in res.stdout.splitlines() if line.strip()]
        details["kubernetes_namespaces"] = namespaces
        ctx = run_cmd(["kubectl", "config", "current-context"], timeout=3)
        if ctx.ok:
            details["kubernetes_context"] = ctx.stdout.strip()
        can_i = run_cmd(
            ["kubectl", "auth", "can-i", "patch", "deployments", "--request-timeout=3s"], timeout=6
        )
        caps["kubernetes_write"] = can_i.ok and can_i.stdout.strip().lower().startswith("yes")


def _probe_aws(caps: dict, details: dict) -> None:
    caps["aws_cli"] = which("aws")
    env_creds = bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))
    file_creds = (Path.home() / ".aws" / "credentials").is_file() or (Path.home() / ".aws" / "config").is_file()
    caps["aws_credentials"] = env_creds or file_creds
    caps["aws_read"] = False
    if caps["aws_cli"] and caps["aws_credentials"]:
        res = run_cmd(["aws", "sts", "get-caller-identity", "--output", "json"], timeout=8)
        caps["aws_read"] = res.ok
        if res.ok:
            from cirdan.util import parse_json

            ident = parse_json(res.stdout)
            if isinstance(ident, dict):
                details["aws_account"] = ident.get("Account")
                details["aws_arn"] = ident.get("Arn")


def _probe_cloud_cli(caps: dict) -> None:
    caps["azure_cli"] = which("az")
    caps["azure_config"] = (Path.home() / ".azure").is_dir()
    caps["gcloud_cli"] = which("gcloud")
    caps["gcloud_config"] = (Path.home() / ".config" / "gcloud").is_dir()


def _probe_systemd(caps: dict) -> None:
    caps["systemd"] = which("systemctl") and Path("/run/systemd/system").exists()
    caps["journald"] = which("journalctl")


def _probe_prometheus(caps: dict, details: dict, config: CirdanConfig) -> None:
    caps["prometheus_read"] = False
    candidates = []
    if config.telemetry.prometheus_url:
        candidates.append(config.telemetry.prometheus_url)
    if os.environ.get("PROMETHEUS_URL"):
        candidates.append(os.environ["PROMETHEUS_URL"])
    candidates.append("http://localhost:9090")
    for url in candidates:
        try:
            resp = httpx.get(url.rstrip("/") + "/-/ready", timeout=1.5)
            if resp.status_code < 500:
                caps["prometheus_read"] = True
                details["prometheus_url"] = url.rstrip("/")
                return
        except httpx.HTTPError:
            continue


def detect_access(config: CirdanConfig) -> AccessContext:
    root = config.root_path
    caps: dict[str, bool] = {}
    details: dict = {}

    caps["file_read"], caps["file_write"] = _probe_files(root)
    caps["shell"] = which("sh") or which("bash")
    _probe_docker(caps, details)
    _probe_kubernetes(caps, details)
    _probe_aws(caps, details)
    _probe_cloud_cli(caps)
    _probe_systemd(caps)
    _probe_prometheus(caps, details, config)

    return AccessContext(
        source={
            "agent": _detect_agent(),
            "workspace": str(root),
            "user": os.environ.get("USER", "unknown"),
        },
        capabilities=caps,
        details=details,
    )


def render_access_report(ctx: AccessContext) -> str:
    caps = ctx.capabilities

    def yn(key: str) -> str:
        return "yes" if caps.get(key) else "no"

    lines = [
        "Cirdan Access Report",
        "",
        f"Source: {ctx.source.get('agent')} (workspace {ctx.source.get('workspace')})",
        f"Mode:   {ctx.mode}",
        "",
        "Files:",
        f"  repo read: {yn('file_read')}",
        f"  repo write: {yn('file_write')}",
        "",
        "Shell:",
        f"  available: {yn('shell')}",
        "",
        "Docker:",
        f"  docker socket: {yn('docker_socket')}",
        f"  docker CLI: {yn('docker_cli')}",
        f"  daemon reachable: {yn('docker_read')}",
        "",
        "Kubernetes:",
        f"  kubeconfig: {yn('kubeconfig')}",
        f"  in-cluster service account: {yn('kubernetes_in_cluster')}",
        f"  read access: {yn('kubernetes_read')}",
        f"  write access: {yn('kubernetes_write')}",
    ]
    namespaces = ctx.details.get("kubernetes_namespaces")
    if namespaces:
        lines.append(f"  namespaces visible: {', '.join(namespaces)}")
    lines += [
        "",
        "Cloud:",
        f"  AWS identity: {'detected' if caps.get('aws_read') else 'not detected'}",
        f"  Azure identity: {'possible' if caps.get('azure_config') else 'not detected'}",
        f"  GCP identity: {'possible' if caps.get('gcloud_config') else 'not detected'}",
        "",
        "Telemetry:",
        f"  prometheus: {ctx.details.get('prometheus_url', 'not detected')}",
        f"  journald: {yn('journald')}",
        "",
        "Runtime:",
        f"  systemd: {yn('systemd')}",
        f"  docker: {yn('docker_read')}",
        f"  kubernetes: {yn('kubernetes_read')}",
    ]
    return "\n".join(lines) + "\n"
