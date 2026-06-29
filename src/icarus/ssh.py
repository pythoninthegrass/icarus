from __future__ import annotations

import docker
import paramiko
import sys
from icarus.config import config
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icarus.client import DokployClient

TRAEFIK_DYNAMIC_DIR = "/etc/dokploy/traefik/dynamic"


def get_ssh_config() -> dict:
    """Read SSH connection settings from environment."""
    host: str = config("DOKPLOY_SSH_HOST", default="")  # type: ignore[assignment]
    if not host:
        print("ERROR: DOKPLOY_SSH_HOST is required for exec/logs commands.")
        print("  Set it in .env or as an environment variable.")
        sys.exit(1)
    user: str = config("DOKPLOY_SSH_USER", default="root")  # type: ignore[assignment]
    port: str = config("DOKPLOY_SSH_PORT", default="22")  # type: ignore[assignment]
    return {"host": host, "user": user, "port": int(port)}


def build_docker_url(ssh_cfg: dict) -> str:
    """Build an ssh:// URL for docker-py from SSH config."""
    user = ssh_cfg.get("user", "root")
    host = ssh_cfg["host"]
    port = ssh_cfg.get("port", 22)
    if port != 22:
        return f"ssh://{user}@{host}:{port}"
    return f"ssh://{user}@{host}"


def get_docker_client(ssh_cfg: dict) -> docker.DockerClient:
    """Create a Docker client connected via SSH."""
    url = build_docker_url(ssh_cfg)
    return docker.DockerClient(base_url=url, use_ssh_client=True)


def collect_domains(cfg: dict) -> set[str]:
    """Extract all configured domain hostnames from app definitions."""
    domains: set[str] = set()
    for app_def in cfg.get("apps", []):
        domain_cfg = app_def.get("domain")
        if not domain_cfg:
            continue
        domain_list = domain_cfg if isinstance(domain_cfg, list) else [domain_cfg]
        for dom in domain_list:
            domains.add(dom["host"])
    return domains


def find_stale_app_names(current_app_names: set[str], domains: set[str], traefik_files: dict[str, str]) -> set[str]:
    """Identify app names with traefik configs routing to our domains but not in current state.

    Args:
        current_app_names: appNames from the current deployment state.
        domains: hostnames this deployment owns.
        traefik_files: mapping of appName -> content/Host rules from traefik config files.

    Returns:
        Set of stale app names to clean up.
    """
    if not domains:
        return set()
    stale: set[str] = set()
    for app_name, content in traefik_files.items():
        if app_name in current_app_names:
            continue
        if not app_name.startswith("app-"):
            continue
        for domain in domains:
            if f"Host(`{domain}`)" in content:
                stale.add(app_name)
                break
    return stale


def _ssh_exec(ssh: paramiko.SSHClient, cmd: str) -> str:
    """Run a command over SSH and return stdout."""
    _, stdout, _ = ssh.exec_command(cmd)
    return stdout.read().decode().strip()


def cleanup_stale_routes(state: dict, cfg: dict) -> None:
    """Remove traefik configs and docker services for orphaned deployments.

    Skips gracefully if all three DOKPLOY_SSH_ env vars are missing.
    """
    host: str = config("DOKPLOY_SSH_HOST", default="")  # type: ignore[assignment]
    user: str = config("DOKPLOY_SSH_USER", default="")  # type: ignore[assignment]
    port: str = config("DOKPLOY_SSH_PORT", default="")  # type: ignore[assignment]
    if not host and not user and not port:
        print("  Cleanup: skipping (DOKPLOY_SSH_* env vars not set)")
        return
    if not host:
        print("  Cleanup: skipping (DOKPLOY_SSH_HOST is required)")
        return

    domains = collect_domains(cfg)
    if not domains:
        return

    current_app_names = {info["appName"] for info in state["apps"].values()}
    ssh_user = user or "root"
    ssh_port = int(port) if port else 22

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=ssh_port, username=ssh_user)

        traefik_files: dict[str, str] = {}
        file_list = _ssh_exec(ssh, f"ls {TRAEFIK_DYNAMIC_DIR}/*.yml 2>/dev/null")
        for filepath in file_list.splitlines():
            app_name = filepath.rsplit("/", 1)[-1].removesuffix(".yml")
            content = _ssh_exec(ssh, f"cat {filepath}")
            traefik_files[app_name] = content

        stale = find_stale_app_names(current_app_names, domains, traefik_files)
        if not stale:
            return

        print(f"  Cleaning up {len(stale)} stale route(s)...")
        for app_name in sorted(stale):
            config_path = f"{TRAEFIK_DYNAMIC_DIR}/{app_name}.yml"
            _ssh_exec(ssh, f"rm -f {config_path}")
            _ssh_exec(ssh, f"docker service rm {app_name} 2>/dev/null")
            print(f"    Removed: {app_name}")
    finally:
        ssh.close()


def get_containers(client: DokployClient, app_name: str) -> list[dict]:
    """Fetch containers for an app via the Dokploy API."""
    return client.get(
        "docker.getContainersByAppNameMatch",
        params={"appName": app_name},
    )


def resolve_app_for_exec(state: dict, app_name: str | None) -> str:
    """Resolve an app name argument to a Dokploy appName from state.

    If app_name is None and only one app exists, auto-selects it.
    """
    apps = state["apps"]
    if app_name is None:
        if len(apps) == 1:
            return next(iter(apps.values()))["appName"]
        names = ", ".join(sorted(apps.keys()))
        print(f"ERROR: Multiple apps found — specify an app: {names}")
        sys.exit(1)
    if app_name not in apps:
        names = ", ".join(sorted(apps.keys()))
        print(f"ERROR: Unknown app '{app_name}'. Available: {names}")
        sys.exit(1)
    return apps[app_name]["appName"]


def select_container(containers: list[dict], exited: bool, for_exec: bool = False) -> dict:
    """Pick a container from the list.

    Default: return the most recent active container (for logs) or running container (for exec).
    With exited=True: show a numbered list and prompt for selection.
    """
    if not containers:
        print("ERROR: No containers found for this app.")
        sys.exit(1)

    if exited:
        for i, c in enumerate(containers, 1):
            print(f"  {i}) {c['name']}  ({c['containerId'][:12]})  [{c['state']}]")
        while True:
            try:
                choice = int(input("Select container: "))
                if 1 <= choice <= len(containers):
                    return containers[choice - 1]
            except (ValueError, EOFError):
                pass
            print(f"  Enter a number between 1 and {len(containers)}.")
    elif for_exec:
        running = [c for c in containers if c["state"] == "running"]
        if not running:
            print("ERROR: No running container found. Use --exited to pick from exited containers.")
            sys.exit(1)
        return running[0]
    else:
        running = [c for c in containers if c["state"] == "running"]
        return running[0] if running else containers[0]
