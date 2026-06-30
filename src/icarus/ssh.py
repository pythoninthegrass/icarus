from __future__ import annotations

import docker
import paramiko
import shlex
import sys
from icarus.config import config
from icarus.env import parse_env_to_dict
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


def _open_ssh(host: str, user: str, port: int) -> paramiko.SSHClient:
    """Open an SSH connection with automatic host-key acceptance."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user)
    return ssh


def update_service_env(ssh: paramiko.SSHClient, app_name: str, env_vars: dict[str, str]) -> None:
    """Apply env vars into a running Docker swarm service via --env-add.

    Uses ``docker service update --env-add`` which is incremental: existing
    spec fields (mounts, networks, resources, labels) are preserved and the
    service only restarts if the effective env actually changed.
    """
    if not env_vars:
        return
    env_args = " ".join(f"--env-add {shlex.quote(f'{k}={v}')}" for k, v in env_vars.items())
    _ssh_exec(ssh, f"docker service update {env_args} {shlex.quote(app_name)}")


def sync_service_envs(state: dict, app_envs: dict[str, str]) -> None:
    """Apply resolved env strings into live Docker swarm service specs over SSH.

    Skips gracefully when DOKPLOY_SSH_HOST is not configured.  Only processes
    application (non-compose) apps; compose apps re-read env on redeploy.
    Opens a single SSH connection for all apps.
    """
    if not app_envs:
        return

    host: str = config("DOKPLOY_SSH_HOST", default="")  # type: ignore[assignment]
    if not host:
        print("  Sync: skipping (DOKPLOY_SSH_HOST not set)")
        return

    user: str = config("DOKPLOY_SSH_USER", default="root")  # type: ignore[assignment]
    port_str: str = config("DOKPLOY_SSH_PORT", default="22")  # type: ignore[assignment]
    ssh_port = int(port_str) if port_str else 22

    ssh = _open_ssh(host, user or "root", ssh_port)
    try:
        for name, env_content in app_envs.items():
            app_info = state["apps"].get(name, {})
            if app_info.get("source") == "compose":
                continue
            app_name = app_info.get("appName", "")
            if not app_name:
                continue
            env_vars = parse_env_to_dict(env_content)
            if not env_vars:
                continue
            update_service_env(ssh, app_name, env_vars)
            print(f"  Synced env into service {app_name}")
    finally:
        ssh.close()


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

    ssh = _open_ssh(host, ssh_user, ssh_port)
    try:
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
