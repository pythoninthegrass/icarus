#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#     "docker[ssh]>=7.0",
#     "httpx>=0.28.1,<1",
#     "python-decouple>=3.8",
#     "pyyaml>=6.0",
# ]
# [tool.uv]
# exclude-newer = "2026-03-31T00:00:00Z"
# ///

# pyright: reportMissingImports=false
# type: ignore[import-untyped]

"""
Dokploy deployment script — config-driven via dokploy.yml.

Usage:
    dps check
    dps --env <environment> <setup|env|deploy|status|destroy>
    dps --env <environment> logs [app] [-f] [-n TAIL] [--exited]
    dps --env <environment> exec [app] [--exited] [-- command...]

Environment can also be set via DOKPLOY_ENV env var.
SSH commands (logs, exec) require DOKPLOY_SSH_HOST in .env.
"""

import argparse
import copy
import docker
import httpx
import json
import re
import sys
import yaml
from decouple import Config, RepositoryEnv
from pathlib import Path


def _build_config() -> Config:
    """Build a decouple Config that reads .env from the current working directory."""
    env_file = Path.cwd() / ".env"
    return Config(RepositoryEnv(str(env_file)))


config = _build_config()


def find_repo_root() -> Path:
    """Walk up from cwd looking for dokploy.yml."""
    current = Path.cwd()
    while True:
        if (current / "dokploy.yml").exists():
            return current
        parent = current.parent
        if parent == current:
            # Reached filesystem root without finding config
            print("ERROR: Could not find dokploy.yml in any parent directory.")
            sys.exit(1)
        current = parent


DEFAULT_ENV_EXCLUDE_PREFIXES = [
    "COMPOSE_",
    "CONTAINER_NAME",
    "DOKPLOY_",
    "DOPPLER_",
    "PGDATA",
    "POSTGRES_VERSION",
    "TASK_X_",
]


def get_state_file(repo_root: Path, env_name: str) -> Path:
    """Return path to the state file for the given environment."""
    return repo_root / ".dokploy-state" / f"{env_name}.json"


def load_config(repo_root: Path) -> dict:
    """Read and return dokploy.yml from the repo root."""
    config_file = repo_root / "dokploy.yml"
    if not config_file.exists():
        print(f"ERROR: Config file not found: {config_file}")
        sys.exit(1)
    with config_file.open() as f:
        return yaml.safe_load(f)


def validate_config(cfg: dict) -> None:
    """Fail fast on invalid config references."""
    app_names = {a["name"] for a in cfg["apps"]}

    for target in cfg["project"].get("env_targets", []):
        if target not in app_names:
            print(f"ERROR: env_targets references unknown app '{target}'")
            sys.exit(1)

    for wave in cfg["project"].get("deploy_order", []):
        for name in wave:
            if name not in app_names:
                print(f"ERROR: deploy_order references unknown app '{name}'")
                sys.exit(1)

    github_apps = [a for a in cfg["apps"] if a.get("source") == "github"]
    if github_apps and "github" not in cfg:
        print("ERROR: GitHub-sourced apps exist but no [github] config found")
        sys.exit(1)


def validate_env_references(cfg: dict) -> None:
    """Check that environment app overrides reference apps that exist in the base config."""
    app_names = {a["name"] for a in cfg["apps"]}
    environments = cfg.get("environments", {})

    for env_name, env_cfg in environments.items():
        for app_name in env_cfg.get("apps", {}):
            if app_name not in app_names:
                print(f"ERROR: environments.{env_name}.apps references unknown app '{app_name}'")
                sys.exit(1)


def merge_env_overrides(cfg: dict, env_name: str) -> dict:
    """Deep-copy config and merge environment-specific overrides into it."""
    merged = copy.deepcopy(cfg)
    environments = merged.pop("environments", {})

    env_overrides = environments.get(env_name, {})

    # Merge github overrides
    if "github" in env_overrides and "github" in merged:
        merged["github"].update(env_overrides["github"])

    # Merge per-app overrides
    app_overrides = env_overrides.get("apps", {})
    for app_def in merged["apps"]:
        name = app_def["name"]
        if name in app_overrides:
            app_def.update(app_overrides[name])

    return merged


def resolve_refs(template: str, state: dict) -> str:
    """Replace {app_name} placeholders with Dokploy appName from state."""

    def replacer(match: re.Match) -> str:
        ref = match.group(1)
        if ref in state["apps"]:
            return state["apps"][ref]["appName"]
        return match.group(0)  # leave unresolved refs as-is

    return re.sub(r"\{(\w+)\}", replacer, template)


def get_env_exclude_prefixes() -> list[str]:
    """Merge default exclude prefixes with optional extras from .env."""
    prefixes = list(DEFAULT_ENV_EXCLUDE_PREFIXES)
    extras: str = config("ENV_EXCLUDE_PREFIXES", default="")  # type: ignore[assignment]
    if extras:
        prefixes.extend(p.strip() for p in extras.split(",") if p.strip())
    return prefixes


def filter_env(content: str, exclude_prefixes: list[str]) -> str:
    """Strip comments, blank lines, and lines whose key starts with an excluded prefix."""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split("=", 1)[0].strip()
        if any(key.startswith(prefix) for prefix in exclude_prefixes):
            continue
        lines.append(line)
    return "\n".join(lines) + "\n" if lines else ""


def build_github_provider_payload(app_id: str, app_def: dict, github_cfg: dict, github_id: str) -> dict:
    """Build payload for application.saveGithubProvider."""
    return {
        "applicationId": app_id,
        "repository": github_cfg["repository"],
        "branch": github_cfg["branch"],
        "owner": github_cfg["owner"],
        "buildPath": app_def.get("buildPath", "/"),
        "githubId": github_id,
        "enableSubmodules": False,
        "triggerType": app_def.get("triggerType", "push"),
        "watchPaths": app_def.get("watchPaths"),
    }


def resolve_github_provider(client: "DokployClient", providers: list[dict], owner: str) -> str:
    """Find the GitHub provider that has access to repos owned by `owner`."""
    for p in providers:
        gid = p["githubId"]
        repos = client.get("github.getGithubRepositories", params={"githubId": gid})
        owners = {r["owner"]["login"] for r in repos}
        if owner in owners:
            return gid
    available = [p["githubId"] for p in providers]
    raise SystemExit(
        f"ERROR: No GitHub provider has access to owner '{owner}'.\n"
        f"  Available providers: {available}\n"
        f"  Configure access in Dokploy UI."
    )


def build_build_type_payload(app_id: str, app_def: dict) -> dict:
    """Build payload for application.saveBuildType."""
    build_type = app_def.get("buildType", "dockerfile")
    payload: dict = {
        "applicationId": app_id,
        "buildType": build_type,
        "dockerContextPath": app_def.get("dockerContextPath", ""),
        "dockerBuildStage": app_def.get("dockerBuildStage", ""),
        "herokuVersion": None,
        "railpackVersion": None,
    }
    if build_type == "dockerfile":
        payload["dockerfile"] = app_def.get("dockerfile", "Dockerfile")
    elif build_type == "static":
        payload["publishDirectory"] = app_def.get("publishDirectory", "")
        payload["isStaticSpa"] = app_def.get("isStaticSpa", False)
    return payload


def build_domain_payload(app_id: str, dom: dict) -> dict:
    """Build payload for domain.create."""
    payload = {
        "applicationId": app_id,
        "host": dom["host"],
        "port": dom["port"],
        "https": dom["https"],
        "certificateType": dom["certificateType"],
    }
    for key in ("path", "internalPath", "stripPath"):
        if key in dom:
            payload[key] = dom[key]
    return payload


def build_app_settings_payload(app_id: str, app_def: dict) -> dict | None:
    """Build payload for application.update (autoDeploy, replicas).

    Returns None if no settings need updating.
    """
    payload: dict = {"applicationId": app_id}
    for key in ("autoDeploy", "replicas"):
        if key in app_def:
            payload[key] = app_def[key]
    return payload if len(payload) > 1 else None


class DokployClient:
    """Thin httpx wrapper for Dokploy API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key},
            timeout=60.0,
        )

    def get(self, path: str, params: dict | None = None) -> dict | list:
        resp = self.client.get(f"/api/{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict | None = None) -> dict:
        resp = self.client.post(f"/api/{path}", json=payload or {})
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()


def validate_state(client: DokployClient, state: dict) -> bool:
    """Check if the project in state still exists on the server.

    Returns True if valid or if the server can't be reached (assume valid).
    Returns False if the project is confirmed gone.
    """
    try:
        projects = client.get("project.all")
    except httpx.HTTPStatusError:
        return True
    return any(p["projectId"] == state["projectId"] for p in projects)


def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        print(f"ERROR: State file not found: {state_file}")
        print("Run 'setup' first.")
        sys.exit(1)
    return json.loads(state_file.read_text())


def save_state(state: dict, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2) + "\n")
    print(f"State saved to {state_file}")


def cmd_check(repo_root: Path) -> None:
    """Pre-flight checks: env vars, server reachability, API auth, config."""
    passed = 0
    failed = 0

    def _pass(label: str, detail: str = "") -> None:
        nonlocal passed
        passed += 1
        msg = f"  PASS  {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    def _fail(label: str, detail: str = "") -> None:
        nonlocal failed
        failed += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    def _warn(label: str, detail: str = "") -> None:
        msg = f"  WARN  {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    def _skip(label: str, detail: str = "") -> None:
        msg = f"  SKIP  {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    print("Running pre-flight checks...\n")

    # 1. Env vars
    api_key = None
    try:
        api_key = config("DOKPLOY_API_KEY")
        _pass("DOKPLOY_API_KEY is set")
    except Exception:
        _fail("DOKPLOY_API_KEY is not set")

    base_url = None
    try:
        base_url = config("DOKPLOY_URL", default="https://dokploy.example.com")
        if base_url == "https://dokploy.example.com":
            _warn(
                "DOKPLOY_URL",
                "using default placeholder (https://dokploy.example.com)",
            )
        else:
            _pass("DOKPLOY_URL", base_url)
    except Exception:
        _fail("DOKPLOY_URL is not set")

    # 2. URL reachability
    if base_url and base_url != "https://dokploy.example.com":
        try:
            resp = httpx.get(base_url, timeout=10.0, follow_redirects=True)
            _pass("Server reachable", f"HTTP {resp.status_code}")
        except httpx.ConnectError:
            _fail("Server unreachable", f"cannot connect to {base_url}")
        except httpx.TimeoutException:
            _fail("Server unreachable", f"timeout connecting to {base_url}")
        except Exception as exc:
            _fail("Server reachable", str(exc))
    else:
        _skip(
            "Server reachability",
            "no valid DOKPLOY_URL configured",
        )

    # 3. API key validity
    if api_key and base_url and base_url != "https://dokploy.example.com":
        try:
            resp = httpx.get(
                f"{base_url.rstrip('/')}/api/project.all",
                headers={"x-api-key": api_key},
                timeout=10.0,
            )
            if resp.status_code == 200:
                _pass("API key valid", "authenticated successfully")
            else:
                _fail(
                    "API key invalid",
                    f"HTTP {resp.status_code}",
                )
        except Exception as exc:
            _fail("API key check", str(exc))
    else:
        _skip(
            "API key validation",
            "missing DOKPLOY_API_KEY or DOKPLOY_URL",
        )

    # 4. Config file
    config_path = repo_root / "dokploy.yml"
    if config_path.exists():
        try:
            with config_path.open() as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                _fail("dokploy.yml", "file does not contain a YAML mapping")
            else:
                missing = [k for k in ("project", "apps") if k not in data]
                if missing:
                    _fail(
                        "dokploy.yml",
                        f"missing required keys: {', '.join(missing)}",
                    )
                else:
                    _pass("dokploy.yml", "valid with project and apps keys")
        except yaml.YAMLError as exc:
            _fail("dokploy.yml", f"YAML parse error: {exc}")
    else:
        _fail("dokploy.yml", f"not found at {config_path}")

    # Summary
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


def cmd_setup(client: DokployClient, cfg: dict, state_file: Path) -> None:
    if state_file.exists():
        print(f"ERROR: State file already exists: {state_file}")
        print("Run 'destroy' first or delete the state file manually.")
        sys.exit(1)

    project_cfg = cfg["project"]
    github_cfg = cfg.get("github")

    # 1. Create project
    print("Creating project...")
    project = client.post(
        "project.create",
        {"name": project_cfg["name"], "description": project_cfg["description"]},
    )
    project_id = project["project"]["projectId"]
    environment_id = project["environment"]["environmentId"]
    print(f"  Project created: {project_id}")
    print(f"  Environment ID: {environment_id}")

    # 2. Get githubId (only if there are GitHub-sourced apps)
    github_id = None
    if github_cfg:
        print("Fetching GitHub provider ID...")
        providers = client.get("github.githubProviders")
        if not providers:
            print("ERROR: No GitHub provider found. Configure one in Dokploy UI first.")
            sys.exit(1)
        github_id = resolve_github_provider(client, providers, github_cfg["owner"])
        print(f"  GitHub ID: {github_id}")

    state: dict = {
        "projectId": project_id,
        "environmentId": environment_id,
        "apps": {},
    }

    # 3. Create apps
    for app_def in cfg["apps"]:
        name = app_def["name"]
        print(f"Creating app: {name}...")
        result = client.post(
            "application.create",
            {"name": name, "environmentId": environment_id},
        )
        app_id = result["applicationId"]
        app_name = result["appName"]
        state["apps"][name] = {"applicationId": app_id, "appName": app_name}
        print(f"  {name}: id={app_id} appName={app_name}")

    # 4. Configure providers
    for app_def in cfg["apps"]:
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]

        if app_def["source"] == "docker":
            print(f"Configuring Docker provider for {name}...")
            client.post(
                "application.saveDockerProvider",
                {
                    "applicationId": app_id,
                    "dockerImage": app_def["dockerImage"],
                    "username": None,
                    "password": None,
                    "registryUrl": None,
                },
            )
        elif app_def["source"] == "github":
            assert github_cfg is not None
            print(f"Configuring GitHub provider for {name}...")
            provider_payload = build_github_provider_payload(app_id, app_def, github_cfg, github_id)
            client.post("application.saveGithubProvider", provider_payload)

            build_type = app_def.get("buildType", "dockerfile")
            print(f"  Setting buildType={build_type} for {name}...")
            build_payload = build_build_type_payload(app_id, app_def)
            client.post("application.saveBuildType", build_payload)

    # 5. Command overrides (resolve {ref} placeholders)
    for app_def in cfg["apps"]:
        name = app_def["name"]
        command = app_def.get("command")
        if not command:
            continue
        resolved = resolve_refs(command, state)
        app_id = state["apps"][name]["applicationId"]
        print(f"Setting command override for {name}...")
        client.post(
            "application.update",
            {"applicationId": app_id, "command": resolved},
        )

    # 6. Domains
    for app_def in cfg["apps"]:
        name = app_def["name"]
        domain_cfg = app_def.get("domain")
        if not domain_cfg:
            continue

        # Support single dict or list of dicts
        domains = domain_cfg if isinstance(domain_cfg, list) else [domain_cfg]
        app_id = state["apps"][name]["applicationId"]
        for dom in domains:
            print(f"Creating domain for {name}: {dom['host']}...")
            domain_payload = build_domain_payload(app_id, dom)
            client.post("domain.create", domain_payload)

    # 7. Application settings (autoDeploy, replicas)
    for app_def in cfg["apps"]:
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        settings_payload = build_app_settings_payload(app_id, app_def)
        if settings_payload:
            print(f"Updating app settings for {name}...")
            client.post("application.update", settings_payload)

    # 8. Save state
    save_state(state, state_file)
    print("\nSetup complete!")
    print(f"  Project: {project_id}")
    for name, info in state["apps"].items():
        print(f"  {name}: {info['applicationId']}")


def cmd_env(client: DokployClient, cfg: dict, state_file: Path, repo_root: Path) -> None:
    state = load_state(state_file)
    env_targets = cfg["project"].get("env_targets", [])
    env_file = repo_root / ".env"
    apps_by_name = {a["name"]: a for a in cfg["apps"]}

    # Read and filter .env for env_targets
    if env_targets:
        if not env_file.exists():
            print(f"ERROR: {env_file} not found.")
            sys.exit(1)

        raw_env = env_file.read_text()
        exclude_prefixes = get_env_exclude_prefixes()
        filtered = filter_env(raw_env, exclude_prefixes)
        total = len(filtered.strip().splitlines()) if filtered.strip() else 0
        print(f"Filtered .env: {total} vars (from {len(raw_env.splitlines())} lines)")

        for name in env_targets:
            app_id = state["apps"][name]["applicationId"]
            create_env_file = apps_by_name[name].get("create_env_file", False)
            print(f"Pushing env vars to {name}...")
            client.post(
                "application.saveEnvironment",
                {
                    "applicationId": app_id,
                    "env": filtered,
                    "buildArgs": None,
                    "buildSecrets": None,
                    "createEnvFile": create_env_file,
                },
            )

    # Push per-app custom env (with {ref} resolution)
    for app_def in cfg["apps"]:
        custom_env = app_def.get("env")
        if not custom_env:
            continue
        name = app_def["name"]
        resolved = resolve_refs(custom_env, state)
        app_id = state["apps"][name]["applicationId"]
        create_env_file = app_def.get("create_env_file", False)
        print(f"Pushing custom env to {name}...")
        client.post(
            "application.saveEnvironment",
            {
                "applicationId": app_id,
                "env": resolved,
                "buildArgs": None,
                "buildSecrets": None,
                "createEnvFile": create_env_file,
            },
        )

    print("\nEnvironment variables pushed.")


def cmd_trigger(client: DokployClient, cfg: dict, state_file: Path) -> None:
    state = load_state(state_file)
    deploy_order = cfg["project"].get("deploy_order", [])

    for i, wave in enumerate(deploy_order, 1):
        print(f"Wave {i}: {', '.join(wave)}")
        for name in wave:
            app_id = state["apps"][name]["applicationId"]
            print(f"  Deploying {name}...")
            client.post("application.deploy", {"applicationId": app_id})
            print(f"    {name} deploy triggered.")

    print("\nAll deploys triggered.")


def cmd_deploy(repo_root: Path, client: DokployClient, cfg: dict, state_file: Path) -> None:
    print("\n==> Phase 1/4: check")
    cmd_check(repo_root)

    if state_file.exists():
        state = load_state(state_file)
        if validate_state(client, state):
            print("\n==> Phase 2/4: setup (skipped, state file exists)")
        else:
            print("\n==> Phase 2/4: setup (state orphaned, recreating)")
            state_file.unlink()
            cmd_setup(client, cfg, state_file)
    else:
        print("\n==> Phase 2/4: setup")
        cmd_setup(client, cfg, state_file)

    print("\n==> Phase 3/4: env")
    cmd_env(client, cfg, state_file, repo_root)

    print("\n==> Phase 4/4: trigger")
    cmd_trigger(client, cfg, state_file)


def cmd_status(client: DokployClient, state_file: Path) -> None:
    state = load_state(state_file)

    print(f"Project: {state['projectId']}")
    print()
    for name, info in state["apps"].items():
        app: dict = client.get("application.one", {"applicationId": info["applicationId"]})  # type: ignore[assignment]
        status = app.get("applicationStatus", "unknown")
        print(f"  {name:10s}  {status}")


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
        return containers[0]


def cmd_logs(client: DokployClient, state_file: Path, app: str | None, follow: bool, tail: int, exited: bool) -> None:
    """Fetch container logs via docker-py over SSH."""
    state = load_state(state_file)
    dokploy_name = resolve_app_for_exec(state, app)
    ssh_cfg = get_ssh_config()

    containers = get_containers(client, dokploy_name)
    container_info = select_container(containers, exited=exited)
    print(
        f"Container: {container_info['name']} ({container_info['containerId'][:12]}) [{container_info['state']}]",
        file=sys.stderr,
    )

    docker_client = get_docker_client(ssh_cfg)
    try:
        container = docker_client.containers.get(container_info["containerId"])
        tail_arg = tail if tail > 0 else "all"
        if follow:
            for chunk in container.logs(stream=True, follow=True, tail=tail_arg):
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        else:
            output = container.logs(tail=tail_arg)
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()
    except KeyboardInterrupt:
        pass
    finally:
        docker_client.close()


def cmd_exec(client: DokployClient, state_file: Path, app: str | None, exited: bool, command: list[str] | None) -> None:
    """Execute a command in a container via docker-py over SSH."""
    state = load_state(state_file)
    dokploy_name = resolve_app_for_exec(state, app)
    ssh_cfg = get_ssh_config()

    containers = get_containers(client, dokploy_name)
    container_info = select_container(containers, exited=exited, for_exec=True)
    print(
        f"Container: {container_info['name']} ({container_info['containerId'][:12]}) [{container_info['state']}]",
        file=sys.stderr,
    )

    docker_client = get_docker_client(ssh_cfg)
    try:
        container = docker_client.containers.get(container_info["containerId"])
        cmd = command if command else ["sh"]
        exit_code, output = container.exec_run(cmd, stdin=True, tty=True, demux=True)
        if output:
            stdout_data, stderr_data = output
            if stdout_data:
                sys.stdout.buffer.write(stdout_data)
            if stderr_data:
                sys.stderr.buffer.write(stderr_data)
        sys.exit(exit_code)
    finally:
        docker_client.close()


def cmd_destroy(client: DokployClient, state_file: Path) -> None:
    state = load_state(state_file)

    project_id = state["projectId"]
    print(f"Deleting project {project_id} (cascades to all apps)...")
    client.post("project.remove", {"projectId": project_id})
    print("  Project deleted.")

    state_file.unlink(missing_ok=True)
    print("  State file removed.")
    print("\nDestroy complete.")


def cmd_import(client: DokployClient, cfg: dict, state_file: Path) -> None:
    if state_file.exists():
        print(f"ERROR: State file already exists: {state_file}")
        print("Delete the state file first if you want to re-import.")
        sys.exit(1)

    project_name = cfg["project"]["name"]
    print("Fetching projects from server...")
    projects = client.get("project.all")

    matching = [p for p in projects if p["name"] == project_name]
    if not matching:
        print(f"ERROR: No project named '{project_name}' found on the server.")
        sys.exit(1)

    project = matching[0]
    project_id = project["projectId"]
    print(f"  Found project: {project_id}")

    environments = project.get("environments", [])
    if not environments:
        print("ERROR: Project has no environments.")
        sys.exit(1)

    environment = environments[0]
    environment_id = environment["environmentId"]
    print(f"  Environment: {environment_id}")

    server_apps = {app["name"]: app for app in environment.get("applications", [])}

    config_app_names = [app_def["name"] for app_def in cfg["apps"]]
    missing = [name for name in config_app_names if name not in server_apps]
    if missing:
        print(f"ERROR: Apps not found on server: {', '.join(missing)}")
        sys.exit(1)

    state: dict = {
        "projectId": project_id,
        "environmentId": environment_id,
        "apps": {},
    }

    for name in config_app_names:
        srv = server_apps[name]
        state["apps"][name] = {
            "applicationId": srv["applicationId"],
            "appName": srv["appName"],
        }
        print(f"  {name}: id={srv['applicationId']} appName={srv['appName']}")

    save_state(state, state_file)
    print("\nImport complete!")
    print(f"  Project: {project_id}")
    for name, info in state["apps"].items():
        print(f"  {name}: {info['applicationId']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dokploy deployment script — config-driven via dokploy.yml.")
    parser.add_argument(
        "--env",
        default=None,
        help="Target environment (default: DOKPLOY_ENV from .env, or 'dev')",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="Pre-flight checks")
    sub.add_parser("setup", help="Create project + apps")
    sub.add_parser("env", help="Push environment variables")
    sub.add_parser("deploy", help="Full pipeline: check, setup, env, trigger")
    sub.add_parser("trigger", help="Deploy apps in wave order")
    sub.add_parser("status", help="Show deployment status")
    sub.add_parser("destroy", help="Delete project and state file")
    sub.add_parser("import", help="Import existing project from server")

    logs_parser = sub.add_parser("logs", help="View container logs via SSH")
    logs_parser.add_argument("app", nargs="?", default=None, help="App name (auto-selects if only one)")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    logs_parser.add_argument("-n", "--tail", type=int, default=100, help="Number of lines (default: 100, 0 for all)")
    logs_parser.add_argument("--exited", action="store_true", help="Pick from all containers (including exited)")

    exec_parser = sub.add_parser("exec", help="Execute command in container via SSH")
    exec_parser.add_argument("app", nargs="?", default=None, help="App name (auto-selects if only one)")
    exec_parser.add_argument("--exited", action="store_true", help="Pick from all containers (including exited)")
    exec_parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run (default: sh)")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return

    if args.command == "check":
        cmd_check(Path.cwd())
        return

    env_name = args.env or config("DOKPLOY_ENV", default="dev")

    api_key: str = config("DOKPLOY_API_KEY")  # type: ignore[assignment]
    base_url: str = config("DOKPLOY_URL", default="https://dokploy.example.com")  # type: ignore[assignment]
    client = DokployClient(base_url, api_key)

    if args.command in ("logs", "exec"):
        state_file = get_state_file(Path.cwd(), env_name)
        if args.command == "logs":
            cmd_logs(client, state_file, args.app, args.follow, args.tail, args.exited)
        else:
            exec_cmd = args.cmd if args.cmd else None
            if exec_cmd and exec_cmd[0] == "--":
                exec_cmd = exec_cmd[1:]
            cmd_exec(client, state_file, args.app, args.exited, exec_cmd or None)
        return

    repo_root = find_repo_root()
    state_file = get_state_file(repo_root, env_name)

    cfg = load_config(repo_root)
    validate_env_references(cfg)
    cfg = merge_env_overrides(cfg, env_name)
    validate_config(cfg)

    match args.command:
        case "setup":
            cmd_setup(client, cfg, state_file)
        case "env":
            cmd_env(client, cfg, state_file, repo_root)
        case "deploy":
            cmd_deploy(repo_root, client, cfg, state_file)
        case "trigger":
            cmd_trigger(client, cfg, state_file)
        case "status":
            cmd_status(client, state_file)
        case "destroy":
            cmd_destroy(client, state_file)
        case "import":
            cmd_import(client, cfg, state_file)


if __name__ == "__main__":
    main()
