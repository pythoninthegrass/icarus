import re
from decouple import Config, RepositoryEnv
from icarus.config import config
from pathlib import Path

DEFAULT_ENV_EXCLUDES = [
    "COMPOSE_",
    "CONTAINER_NAME",
    "DOKPLOY_",
    "DOPPLER_",
    "PGDATA",
    "POSTGRES_VERSION",
    "TASK_X_",
]


def get_env_excludes() -> list[str]:
    """Merge default exclusion patterns with optional extras from .env.

    Reads ``ENV_EXCLUDE_PREFIXES`` for backward compatibility and the new
    ``ENV_EXCLUDES`` key.  Patterns ending with ``_`` or ``*`` are treated as
    prefix matches; all other patterns are exact matches.
    """
    patterns = list(DEFAULT_ENV_EXCLUDES)
    for env_key in ("ENV_EXCLUDES", "ENV_EXCLUDE_PREFIXES"):
        extras: str = config(env_key, default="")  # type: ignore[assignment]
        if extras:
            patterns.extend(p.strip() for p in extras.split(",") if p.strip())
    return patterns


def _is_env_excluded(key: str, patterns: list[str]) -> bool:
    """Check if an env var key matches any exclusion pattern.

    Patterns ending with ``_`` or ``*`` are prefix matches (the ``*`` is
    stripped before comparison).  All other patterns are exact matches.
    """
    for pattern in patterns:
        if pattern.endswith("*"):
            if key.startswith(pattern[:-1]):
                return True
        elif pattern.endswith("_"):
            if key.startswith(pattern):
                return True
        else:
            if key == pattern:
                return True
    return False


def filter_env(content: str, exclude_patterns: list[str]) -> str:
    """Strip comments, blank lines, and lines whose key matches an exclusion pattern."""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split("=", 1)[0].strip()
        if _is_env_excluded(key, exclude_patterns):
            continue
        lines.append(line)
    return "\n".join(lines) + "\n" if lines else ""


def resolve_env_for_push(env_file: Path, exclude_patterns: list[str]) -> str:
    """Read env file and resolve values, preferring os.environ over file values.

    Uses python-decouple's Config resolution (os.environ > file > default)
    so that ``doppler run -- ic env`` injects secrets without modifying the file.
    """
    repo = RepositoryEnv(str(env_file))
    push_config = Config(repo)
    lines = []
    for key in repo.data:
        if _is_env_excluded(key, exclude_patterns):
            continue
        lines.append(f"{key}={push_config(key)}")
    return "\n".join(lines) + "\n" if lines else ""


def resolve_refs(template: str, state: dict) -> str:
    """Replace {app_name} placeholders with Dokploy appName from state."""

    def replacer(match: re.Match) -> str:
        ref = match.group(1)
        if ref in state["apps"]:
            return state["apps"][ref]["appName"]
        return match.group(0)  # leave unresolved refs as-is

    return re.sub(r"\{(\w+)\}", replacer, template)


def merge_app_env(base: str, custom: str) -> str:
    """Merge per-app env into a shared env string, with per-app keys winning.

    Strips keys from ``base`` that ``custom`` overrides, then appends
    ``custom``.  Duplicate keys always resolve to the ``custom`` value
    regardless of whether the dotenv parser uses first-wins or last-wins
    semantics.
    """
    if not custom:
        return base
    override_keys = {line.split("=", 1)[0].strip() for line in custom.splitlines() if "=" in line and not line.startswith("#")}
    base_lines = [line for line in base.splitlines() if not ("=" in line and line.split("=", 1)[0].strip() in override_keys)]
    return "\n".join(base_lines).rstrip("\n") + "\n" + custom


def parse_env_to_dict(content: str) -> dict[str, str]:
    """Parse a dotenv-format string into a dict.

    Skips comments and blank lines; splits on the first ``=``; last-wins on
    duplicate keys.
    """
    result: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value
    return result


def resolve_app_envs(cfg: dict, state: dict, env_file: Path) -> dict[str, str]:
    """Build a mapping of app_config_name → resolved dotenv string.

    Mirrors cmd_env's resolution logic so that cmd_trigger can push the same
    env into the live Docker service specs.  Returns only apps that have env
    vars to push; returns an empty dict when nothing is configured.
    """
    result: dict[str, str] = {}
    env_targets: list[str] = cfg.get("project", {}).get("env_targets", [])
    env_targets_set = set(env_targets)
    apps_by_name = {a["name"]: a for a in cfg.get("apps", [])}

    if env_targets and env_file.exists():
        exclude_patterns = get_env_excludes()
        filtered = resolve_env_for_push(env_file, exclude_patterns)
        for name in env_targets:
            resolved = resolve_refs(filtered, state)
            app_def = apps_by_name[name]
            custom_env = app_def.get("env")
            if custom_env:
                custom_resolved = resolve_refs(custom_env, state)
                resolved = merge_app_env(resolved, custom_resolved)
            result[name] = resolved

    for app_def in cfg.get("apps", []):
        custom_env = app_def.get("env")
        if not custom_env:
            continue
        name = app_def["name"]
        if name in env_targets_set:
            continue
        result[name] = resolve_refs(custom_env, state)

    return result
