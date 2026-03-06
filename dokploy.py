#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
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
    dokploy.py --env <environment> <setup|env|deploy|status|destroy>

Environment can also be set via DOKPLOY_ENV env var.
"""

import argparse
import copy
import httpx
import json
import re
import sys
import yaml
from decouple import config
from pathlib import Path


def find_repo_root() -> Path:
    """Walk up from the script's directory looking for dokploy.yml."""
    current = Path(__file__).resolve().parent
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
    "DOPPLER_",
    "PGDATA",
    "POSTGRES_VERSION",
    "DOKPLOY_",
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
                print(
                    f"ERROR: environments.{env_name}.apps references "
                    f"unknown app '{app_name}'"
                )
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
        github_id = providers[0]["githubId"]
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
            client.post(
                "application.saveGithubProvider",
                {
                    "applicationId": app_id,
                    "repository": github_cfg["repository"],
                    "branch": github_cfg["branch"],
                    "owner": github_cfg["owner"],
                    "buildPath": "/",
                    "githubId": github_id,
                    "enableSubmodules": False,
                    "triggerType": "push",
                    "watchPaths": None,
                },
            )
            print(f"  Setting buildType=dockerfile for {name}...")
            client.post(
                "application.saveBuildType",
                {
                    "applicationId": app_id,
                    "buildType": "dockerfile",
                    "dockerfile": "Dockerfile",
                    "dockerContextPath": "",
                    "dockerBuildStage": "",
                },
            )

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
            client.post(
                "domain.create",
                {
                    "applicationId": app_id,
                    "host": dom["host"],
                    "port": dom["port"],
                    "https": dom["https"],
                    "certificateType": dom["certificateType"],
                },
            )

    # 7. Save state
    save_state(state, state_file)
    print("\nSetup complete!")
    print(f"  Project: {project_id}")
    for name, info in state["apps"].items():
        print(f"  {name}: {info['applicationId']}")


def cmd_env(
    client: DokployClient, cfg: dict, state_file: Path, repo_root: Path
) -> None:
    state = load_state(state_file)
    env_targets = cfg["project"].get("env_targets", [])
    env_file = repo_root / ".env"

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
            print(f"Pushing env vars to {name}...")
            client.post(
                "application.saveEnvironment",
                {
                    "applicationId": app_id,
                    "env": filtered,
                    "buildArgs": None,
                    "buildSecrets": None,
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
        print(f"Pushing custom env to {name}...")
        client.post(
            "application.saveEnvironment",
            {
                "applicationId": app_id,
                "env": resolved,
                "buildArgs": None,
                "buildSecrets": None,
            },
        )

    print("\nEnvironment variables pushed.")


def cmd_deploy(client: DokployClient, cfg: dict, state_file: Path) -> None:
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


def cmd_status(client: DokployClient, state_file: Path) -> None:
    state = load_state(state_file)

    print(f"Project: {state['projectId']}")
    print()
    for name, info in state["apps"].items():
        app: dict = client.get(
            "application.one", {"applicationId": info["applicationId"]}
        )  # type: ignore[assignment]
        status = app.get("applicationStatus", "unknown")
        print(f"  {name:10s}  {status}")


def cmd_destroy(client: DokployClient, state_file: Path) -> None:
    state = load_state(state_file)

    project_id = state["projectId"]
    print(f"Deleting project {project_id} (cascades to all apps)...")
    client.post("project.remove", {"projectId": project_id})
    print("  Project deleted.")

    state_file.unlink(missing_ok=True)
    print("  State file removed.")
    print("\nDestroy complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dokploy deployment script — config-driven via dokploy.yml."
    )
    parser.add_argument(
        "--env",
        default=None,
        help="Target environment (default: DOKPLOY_ENV from .env, or 'dev')",
    )
    parser.add_argument(
        "command",
        choices=["setup", "env", "deploy", "status", "destroy"],
        help="Command to run",
    )
    args = parser.parse_args()

    env_name = args.env or config("DOKPLOY_ENV", default="dev")

    repo_root = find_repo_root()

    api_key: str = config("DOKPLOY_API_KEY")  # type: ignore[assignment]
    base_url: str = config("DOKPLOY_URL", default="https://dokploy.example.com")  # type: ignore[assignment]

    cfg = load_config(repo_root)
    validate_env_references(cfg)
    cfg = merge_env_overrides(cfg, env_name)
    validate_config(cfg)

    client = DokployClient(base_url, api_key)
    state_file = get_state_file(repo_root, env_name)

    match args.command:
        case "setup":
            cmd_setup(client, cfg, state_file)
        case "env":
            cmd_env(client, cfg, state_file, repo_root)
        case "deploy":
            cmd_deploy(client, cfg, state_file)
        case "status":
            cmd_status(client, state_file)
        case "destroy":
            cmd_destroy(client, state_file)


if __name__ == "__main__":
    main()
