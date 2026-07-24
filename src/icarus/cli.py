"""Dokploy deployment CLI — config-driven via dokploy.yml.

Usage:
    ic check
    ic --env <environment> <setup|env|apply|status|clean|destroy>
    ic --env <environment> logs [app] [-f] [-n TAIL] [--exited]
    ic --env <environment> exec [app] [--exited] [-- command...]
    ic --env <environment> backup <resource> [--prefix NAME] [--list] [--volume NAME]

Environment can also be set via DOKPLOY_ENV env var.
SSH commands (logs, exec) require DOKPLOY_SSH_HOST in .env.
"""

from __future__ import annotations

import argparse
from icarus.client import DokployClient
from icarus.commands import (
    cmd_apply,
    cmd_backup,
    cmd_check,
    cmd_clean,
    cmd_destroy,
    cmd_env,
    cmd_exec,
    cmd_import,
    cmd_logs,
    cmd_run_schedule,
    cmd_setup,
    cmd_status,
    cmd_trigger,
)
from icarus.config import config, find_repo_root
from icarus.plan import cmd_plan
from icarus.schema import (
    get_state_file,
    load_config,
    merge_env_overrides,
    validate_config,
    validate_env_references,
)
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Dokploy deployment script — config-driven via dokploy.yml.")
    parser.add_argument(
        "--env",
        default=None,
        help="Target environment (default: DOKPLOY_ENV from .env, or 'dev')",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        type=Path,
        help="Path to .env file for push (default: DOTENV_FILE env var, or <repo>/.env)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="Pre-flight checks")
    sub.add_parser("plan", help="Show what apply would change without executing")
    sub.add_parser("setup", help="Create project + apps")
    sub.add_parser("env", help="Push environment variables")
    sub.add_parser("apply", help="Full pipeline: check, setup, env, trigger")
    sub.add_parser("trigger", help="Deploy apps in wave order")
    sub.add_parser("status", help="Show deployment status")
    sub.add_parser("clean", help="Remove stale Traefik configs and orphaned Docker services")
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

    run_schedule_parser = sub.add_parser("run-schedule", help="Manually trigger a schedule")
    run_schedule_parser.add_argument("app", nargs="?", default=None, help="App name (auto-selects if only one)")
    run_schedule_parser.add_argument("schedule", help="Schedule name (as defined in dokploy.yml)")

    backup_parser = sub.add_parser("backup", help="Trigger a manual database backup or volume backup")
    backup_parser.add_argument("resource", help="Database, app, or compose name (as defined in dokploy.yml)")
    backup_parser.add_argument("--prefix", default=None, help="Backup schedule prefix (auto-selects if only one)")
    backup_parser.add_argument("--list", action="store_true", dest="list_files", help="List backup files")
    backup_parser.add_argument("--volume", default=None, help="Run the named volume backup instead")

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

    if args.command in ("logs", "exec", "run-schedule", "backup"):
        state_file = get_state_file(Path.cwd(), env_name)
        if args.command == "logs":
            cmd_logs(client, state_file, args.app, args.follow, args.tail, args.exited)
        elif args.command == "exec":
            exec_cmd = args.cmd if args.cmd else None
            if exec_cmd and exec_cmd[0] == "--":
                exec_cmd = exec_cmd[1:]
            cmd_exec(client, state_file, args.app, args.exited, exec_cmd or None)
        elif args.command == "backup":
            cmd_backup(client, state_file, args.resource, args.prefix, args.list_files, args.volume)
        else:
            cmd_run_schedule(client, state_file, args.app, args.schedule)
        return

    repo_root = find_repo_root()
    state_file = get_state_file(repo_root, env_name)

    cfg = load_config(repo_root)
    validate_env_references(cfg)
    cfg = merge_env_overrides(cfg, env_name)
    validate_config(cfg)

    match args.command:
        case "plan":
            cmd_plan(client, cfg, state_file, repo_root)
        case "setup":
            cmd_setup(client, cfg, state_file, repo_root)
        case "env":
            cmd_env(client, cfg, state_file, repo_root, env_file_override=args.env_file)
        case "apply":
            cmd_apply(repo_root, client, cfg, state_file, env_file_override=args.env_file)
        case "trigger":
            cmd_trigger(client, cfg, state_file, repo_root=repo_root, env_file_override=args.env_file, redeploy=True)
        case "status":
            cmd_status(client, state_file)
        case "clean":
            cmd_clean(cfg, state_file)
        case "destroy":
            cmd_destroy(client, cfg, state_file)
        case "import":
            cmd_import(client, cfg, state_file)
