import copy
import sys
import yaml
from pathlib import Path


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

    github_apps = [a for a in cfg["apps"] if a.get("source") == "github" or a.get("sourceType") == "github"]
    if github_apps and "github" not in cfg:
        print("ERROR: GitHub-sourced apps exist but no [github] config found")
        sys.exit(1)

    registry_names = {r["name"] for r in cfg.get("registries", [])}
    for app_def in cfg["apps"]:
        reg_ref = app_def.get("registry")
        if reg_ref and reg_ref not in registry_names:
            print(f"ERROR: App '{app_def['name']}' references unknown registry '{reg_ref}'")
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
