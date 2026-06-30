from __future__ import annotations

import httpx
import os
import sys
import yaml
from icarus.client import load_state, save_state, validate_state
from icarus.config import config
from icarus.env import (
    filter_env,
    get_env_excludes,
    merge_app_env,
    parse_env_to_dict,
    resolve_app_envs,
    resolve_env_for_push,
    resolve_refs,
)
from icarus.payloads import (
    DATABASE_DEFAULTS,
    build_app_settings_payload,
    build_backup_create_payload,
    build_build_type_payload,
    build_certificate_create_payload,
    build_compose_update_payload,
    build_database_create_payload,
    build_destination_create_payload,
    build_destination_update_payload,
    build_domain_payload,
    build_github_provider_payload,
    build_mount_payload,
    build_port_payload,
    build_redirect_payload,
    build_registry_create_payload,
    build_registry_update_payload,
    build_schedule_payload,
    build_security_payload,
    database_endpoint,
    database_id_key,
    is_compose,
    resolve_compose_file,
    resolve_github_provider,
    resolve_registry_id,
)
from icarus.plan import cmd_plan, compute_plan
from icarus.reconcile import (
    reconcile_app_domains,
    reconcile_app_mounts,
    reconcile_app_ports,
    reconcile_app_redirects,
    reconcile_app_registry,
    reconcile_app_schedules,
    reconcile_app_security,
    reconcile_app_settings,
    reconcile_certificates,
    reconcile_database_backups,
    reconcile_destinations,
    reconcile_registries,
)
from icarus.ssh import (
    cleanup_stale_routes,
    get_containers,
    get_docker_client,
    get_ssh_config,
    resolve_app_for_exec,
    select_container,
    sync_service_envs,
)
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icarus.client import DokployClient


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


def cmd_setup(client: DokployClient, cfg: dict, state_file: Path, repo_root: Path | None = None) -> None:
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
    organization_id = project["project"].get("organizationId", "")
    environment_id = project["environment"]["environmentId"]
    print(f"  Project created: {project_id}")
    print(f"  Environment ID: {environment_id}")

    # 2. Get githubId (only if there are GitHub-sourced apps or github-sourced compose apps)
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
        "organizationId": organization_id,
        "environmentId": environment_id,
        "apps": {},
    }

    # 2.5 Registries (server-level, idempotent)
    registries_cfg = cfg.get("registries", [])
    if registries_cfg:
        print("Resolving container registries...")
        existing_registries = client.get("registry.all")
        existing_by_name = {r["registryName"]: r for r in existing_registries}
        state["registries"] = {}
        for reg_def in registries_cfg:
            name = reg_def["name"]
            if name in existing_by_name:
                registry_id = existing_by_name[name]["registryId"]
                print(f"  Registry '{name}' exists: {registry_id}, updating credentials...")
                update_payload = build_registry_update_payload(registry_id, reg_def)
                client.post("registry.update", update_payload)
            else:
                print(f"  Creating registry: {name}...")
                payload = build_registry_create_payload(reg_def)
                resp = client.post("registry.create", payload)
                registry_id = resp["registryId"]
                print(f"  Registry created: {registry_id}")
            state["registries"][name] = {"registryId": registry_id}

    # 2.6 Backup destinations (server-level, idempotent)
    destinations_cfg = cfg.get("destinations", [])
    if destinations_cfg:
        print("Resolving backup destinations...")
        existing_destinations = client.get("destination.all")
        existing_by_name = {d["name"]: d for d in existing_destinations}
        state["destinations"] = {}
        for dest_def in destinations_cfg:
            name = dest_def["name"]
            if name in existing_by_name:
                destination_id = existing_by_name[name]["destinationId"]
                print(f"  Destination '{name}' exists: {destination_id}, updating...")
                update_payload = build_destination_update_payload(destination_id, dest_def)
                client.post("destination.update", update_payload)
            else:
                print(f"  Creating destination: {name}...")
                payload = build_destination_create_payload(dest_def)
                resp = client.post("destination.create", payload)
                destination_id = resp["destinationId"]
                print(f"  Destination created: {destination_id}")
            state["destinations"][name] = {"destinationId": destination_id}

    # 2.7 Certificates (server-level, idempotent)
    certificates_cfg = cfg.get("certificates", [])
    if certificates_cfg:
        print("Resolving certificates...")
        existing_certs = client.get("certificates.all")
        existing_by_name = {c["name"]: c for c in existing_certs}
        state["certificates"] = {}
        effective_root = repo_root or state_file.parent.parent
        for cert_def in certificates_cfg:
            name = cert_def["name"]
            if name in existing_by_name:
                certificate_id = existing_by_name[name]["certificateId"]
                print(f"  Certificate '{name}' exists: {certificate_id}")
            else:
                print(f"  Creating certificate: {name}...")
                payload = build_certificate_create_payload(cert_def, organization_id, effective_root)
                resp = client.post("certificates.create", payload)
                certificate_id = resp["certificateId"]
                print(f"  Certificate created: {certificate_id}")
            state["certificates"][name] = {"certificateId": certificate_id}

    # 3. Create apps
    for app_def in cfg["apps"]:
        name = app_def["name"]
        if is_compose(app_def):
            print(f"Creating compose: {name}...")
            compose_type = app_def.get("composeType", "docker-compose")
            result = client.post(
                "compose.create",
                {
                    "name": name,
                    "environmentId": environment_id,
                    "composeType": compose_type,
                },
            )
            compose_id = result["composeId"]
            app_name = result["appName"]
            state["apps"][name] = {
                "composeId": compose_id,
                "appName": app_name,
                "source": "compose",
            }
            print(f"  {name}: id={compose_id} appName={app_name}")
        else:
            print(f"Creating app: {name}...")
            result = client.post(
                "application.create",
                {"name": name, "environmentId": environment_id},
            )
            app_id = result["applicationId"]
            app_name = result["appName"]
            state["apps"][name] = {"applicationId": app_id, "appName": app_name}
            print(f"  {name}: id={app_id} appName={app_name}")

    # Save state early so destroy can clean up if later steps fail
    save_state(state, state_file, quiet=True)

    # 4. Configure providers
    for app_def in cfg["apps"]:
        name = app_def["name"]

        if is_compose(app_def):
            compose_id = state["apps"][name]["composeId"]
            effective_root = repo_root or state_file.parent.parent
            source_type = app_def.get("sourceType", "raw")
            print(f"Pushing compose source ({source_type}) for {name}...")
            client.post(
                "compose.update",
                build_compose_update_payload(compose_id, app_def, github_cfg, github_id, effective_root),
            )
            continue

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

        # Associate registry if specified
        registry_name = app_def.get("registry")
        if registry_name:
            registry_id = resolve_registry_id(state, registry_name)
            if not registry_id:
                print(f"ERROR: App '{name}' references unknown registry '{registry_name}'")
                sys.exit(1)
            print(f"  Associating registry '{registry_name}' with {name}...")
            client.post("application.update", {"applicationId": app_id, "registryId": registry_id})

    # 5. Command overrides (resolve {ref} placeholders)
    for app_def in cfg["apps"]:
        name = app_def["name"]
        if is_compose(app_def):
            continue
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
        compose = is_compose(app_def)
        resource_id = state["apps"][name]["composeId"] if compose else state["apps"][name]["applicationId"]
        for dom in domains:
            print(f"Creating domain for {name}: {dom['host']}...")
            domain_payload = build_domain_payload(resource_id, dom, compose=compose)
            client.post("domain.create", domain_payload)

    # 7. Application settings (autoDeploy, replicas)
    for app_def in cfg["apps"]:
        if is_compose(app_def):
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        settings_payload = build_app_settings_payload(app_id, app_def)
        if settings_payload:
            print(f"Updating app settings for {name}...")
            client.post("application.update", settings_payload)

    # 8. Volume mounts
    for app_def in cfg["apps"]:
        if is_compose(app_def):
            continue
        volumes = app_def.get("volumes")
        if not volumes:
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        for vol in volumes:
            print(f"Creating mount for {name}: {vol['source']} -> {vol['target']}...")
            mount_payload = build_mount_payload(app_id, vol)
            client.post("mounts.create", mount_payload)

    # 9. Ports
    for app_def in cfg["apps"]:
        if is_compose(app_def):
            continue
        ports = app_def.get("ports")
        if not ports:
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        state["apps"][name]["ports"] = {}
        for port in ports:
            print(f"Creating port for {name}: {port['publishedPort']} -> {port['targetPort']}...")
            port_payload = build_port_payload(app_id, port)
            resp = client.post("port.create", port_payload)
            state["apps"][name]["ports"][port["publishedPort"]] = {"portId": resp["portId"]}

    # 10. Schedules
    for app_def in cfg["apps"]:
        if is_compose(app_def):
            continue
        schedules = app_def.get("schedules")
        if not schedules:
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        state["apps"][name]["schedules"] = {}
        for sched in schedules:
            print(f"Creating schedule for {name}: {sched['name']}...")
            sched_payload = build_schedule_payload(app_id, sched)
            resp = client.post("schedule.create", sched_payload)
            state["apps"][name]["schedules"][sched["name"]] = {"scheduleId": resp["scheduleId"]}

    # 11. Security (basic auth)
    for app_def in cfg["apps"]:
        if is_compose(app_def):
            continue
        security = app_def.get("security")
        if not security:
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        state["apps"][name]["security"] = {}
        for sec in security:
            print(f"Creating security for {name}: {sec['username']}...")
            sec_payload = build_security_payload(app_id, sec)
            resp = client.post("security.create", sec_payload)
            state["apps"][name]["security"][sec["username"]] = {"securityId": resp["securityId"]}

    # 12. Databases
    # 11. Redirects
    for app_def in cfg["apps"]:
        if is_compose(app_def):
            continue
        redirects = app_def.get("redirects")
        if not redirects:
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        state["apps"][name]["redirects"] = {}
        for redir in redirects:
            print(f"Creating redirect for {name}: {redir['regex']}...")
            redir_payload = build_redirect_payload(app_id, redir)
            resp = client.post("redirects.create", redir_payload)
            state["apps"][name]["redirects"][redir["regex"]] = {"redirectId": resp["redirectId"]}

    # 12. Databases
    for db_def in cfg.get("database", []):
        name = db_def["name"]
        db_type = db_def["type"]
        id_key = database_id_key(db_type)
        print(f"Creating {db_type} database: {name}...")
        payload = build_database_create_payload(name, db_def, environment_id)
        resp = client.post(database_endpoint(db_type, "create"), payload)
        db_id = resp[id_key]
        app_name = resp.get("appName", name)
        if "database" not in state:
            state["database"] = {}
        state["database"][name] = {id_key: db_id, "appName": app_name, "type": db_type}
        print(f"  {name}: id={db_id} appName={app_name}")

        print(f"  Deploying {name}...")
        client.post(database_endpoint(db_type, "deploy"), {id_key: db_id})

        # Create backup schedules for this database
        for backup_def in db_def.get("backups", []):
            dest_name = backup_def["destination"]
            dest_id = state.get("destinations", {}).get(dest_name, {}).get("destinationId")
            if not dest_id:
                print(f"ERROR: Backup for '{name}' references unknown destination '{dest_name}'")
                sys.exit(1)
            prefix = backup_def["prefix"]
            print(f"  Creating backup schedule '{prefix}' for {name}...")
            backup_payload = build_backup_create_payload(
                backup_def,
                db_id=db_id,
                db_type=db_type,
                db_name=name,
                destination_id=dest_id,
            )
            backup_resp = client.post("backup.create", backup_payload)
            if "backups" not in state["database"][name]:
                state["database"][name]["backups"] = {}
            state["database"][name]["backups"][prefix] = {"backupId": backup_resp["backupId"]}

    # 13. Save state
    save_state(state, state_file)
    print("\nSetup complete!")
    print(f"  Project: {project_id}")
    for name, info in state["apps"].items():
        rid = info.get("composeId") or info.get("applicationId")
        print(f"  {name}: {rid}")
    for name, info in state.get("database", {}).items():
        id_key = database_id_key(info["type"])
        print(f"  {name}: {info[id_key]} ({info['type']})")


def cmd_env(
    client: DokployClient,
    cfg: dict,
    state_file: Path,
    repo_root: Path,
    env_file_override: Path | None = None,
) -> None:
    state = load_state(state_file)
    env_targets = cfg["project"].get("env_targets", [])
    env_file = env_file_override or Path(os.environ.get("DOTENV_FILE", str(repo_root / ".env")))
    apps_by_name = {a["name"]: a for a in cfg["apps"]}

    env_targets_set = set(env_targets)

    if env_targets:
        if not env_file.exists():
            print(f"ERROR: {env_file} not found.")
            sys.exit(1)

        exclude_patterns = get_env_excludes()
        filtered = resolve_env_for_push(env_file, exclude_patterns)
        total = len(filtered.strip().splitlines()) if filtered.strip() else 0
        print(f"Filtered .env: {total} vars")

        for name in env_targets:
            app_info = state["apps"][name]
            resolved = resolve_refs(filtered, state)

            app_def = apps_by_name[name]
            custom_env = app_def.get("env")
            if custom_env:
                custom_resolved = resolve_refs(custom_env, state)
                resolved = merge_app_env(resolved, custom_resolved)

            if app_info.get("source") == "compose":
                compose_id = app_info["composeId"]
                print(f"Pushing env to compose {name}...")
                if app_def.get("sourceType") == "github":
                    client.post(
                        "compose.update",
                        {"composeId": compose_id, "env": resolved},
                    )
                else:
                    compose_content = resolve_compose_file(app_def, repo_root)
                    client.post(
                        "compose.update",
                        {
                            "composeId": compose_id,
                            "env": resolved,
                            "composeFile": compose_content,
                            "sourceType": "raw",
                        },
                    )
            else:
                app_id = app_info["applicationId"]
                create_env_file = apps_by_name[name].get("create_env_file", False)
                print(f"Pushing env vars to {name}...")
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

    # Push per-app custom env (with {ref} resolution).
    # Skip apps already handled above via env_targets — their per-app env was
    # merged into the shared push and a second saveEnvironment would overwrite it.
    for app_def in cfg["apps"]:
        custom_env = app_def.get("env")
        if not custom_env:
            continue
        name = app_def["name"]
        if name in env_targets_set:
            continue
        resolved = resolve_refs(custom_env, state)
        app_info = state["apps"][name]
        if is_compose(app_def):
            compose_id = app_info["composeId"]
            print(f"Pushing custom env to compose {name}...")
            existing = client.get("compose.one", {"composeId": compose_id})
            prev_env = existing.get("env", "")
            merged = (prev_env.rstrip("\n") + "\n" + resolved).lstrip("\n")
            client.post(
                "compose.update",
                {"composeId": compose_id, "env": merged},
            )
        else:
            app_id = app_info["applicationId"]
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


def cmd_trigger(
    client: DokployClient,
    cfg: dict,
    state_file: Path,
    *,
    repo_root: Path | None = None,
    env_file_override: Path | None = None,
    redeploy: bool = False,
) -> None:
    state = load_state(state_file)

    if redeploy and repo_root is not None:
        env_file = env_file_override or Path(os.environ.get("DOTENV_FILE", str(repo_root / ".env")))
        app_envs = resolve_app_envs(cfg, state, env_file)
        if app_envs:
            print("Syncing env into live service specs...")
            sync_service_envs(state, app_envs)

    deploy_order = cfg["project"].get("deploy_order", [])
    endpoint = "application.redeploy" if redeploy else "application.deploy"
    action = "Redeploying" if redeploy else "Deploying"

    for i, wave in enumerate(deploy_order, 1):
        print(f"Wave {i}: {', '.join(wave)}")
        for name in wave:
            app_info = state["apps"][name]
            print(f"  {action} {name}...")
            if app_info.get("source") == "compose":
                compose_endpoint = "compose.redeploy" if redeploy else "compose.deploy"
                client.post(compose_endpoint, {"composeId": app_info["composeId"]})
            else:
                client.post(endpoint, {"applicationId": app_info["applicationId"]})
            print(f"    {name} deploy triggered.")

    print("\nAll deploys triggered.")


def cmd_apply(
    repo_root: Path,
    client: DokployClient,
    cfg: dict,
    state_file: Path,
    env_file_override: Path | None = None,
) -> None:
    print("\n==> Phase 1/4: check")
    cmd_check(repo_root)

    is_redeploy = False
    if state_file.exists():
        state = load_state(state_file)
        if validate_state(client, state):
            print("\n==> Phase 2/4: setup (skipped, state file exists)")
            is_redeploy = True
        else:
            print("\n==> Phase 2/4: setup (state orphaned, recreating)")
            state_file.unlink()
            cmd_setup(client, cfg, state_file, repo_root)
    else:
        print("\n==> Phase 2/4: setup")
        cmd_setup(client, cfg, state_file, repo_root)

    print("\n==> Phase 3/4: env")
    cmd_env(client, cfg, state_file, repo_root, env_file_override=env_file_override)

    if is_redeploy:
        cleanup_stale_routes(load_state(state_file), cfg)
        reconcile_registries(client, cfg, load_state(state_file), state_file)
        reconcile_destinations(client, cfg, load_state(state_file), state_file)
        reconcile_certificates(client, cfg, load_state(state_file), state_file, repo_root=repo_root)
        reconcile_app_registry(client, cfg, load_state(state_file))
        reconcile_app_domains(client, cfg, load_state(state_file), state_file)
        reconcile_app_schedules(client, cfg, load_state(state_file), state_file)
        reconcile_app_mounts(client, cfg, load_state(state_file), state_file)
        reconcile_app_ports(client, cfg, load_state(state_file), state_file)
        reconcile_app_security(client, cfg, load_state(state_file), state_file)
        reconcile_app_redirects(client, cfg, load_state(state_file), state_file)
        reconcile_app_settings(client, cfg, load_state(state_file))
        reconcile_database_backups(client, cfg, load_state(state_file), state_file)

    print("\n==> Phase 4/4: trigger")
    cmd_trigger(client, cfg, state_file, repo_root=repo_root, env_file_override=env_file_override, redeploy=is_redeploy)


def cmd_status(client: DokployClient, state_file: Path) -> None:
    state = load_state(state_file)

    print(f"Project: {state['projectId']}")
    print()
    for name, info in state["apps"].items():
        if info.get("source") == "compose":
            comp: dict = client.get("compose.one", {"composeId": info["composeId"]})  # type: ignore[assignment]
            status = comp.get("composeStatus", "unknown")
        else:
            app: dict = client.get("application.one", {"applicationId": info["applicationId"]})  # type: ignore[assignment]
            status = app.get("applicationStatus", "unknown")
        print(f"  {name:10s}  {status}")

    for name, info in state.get("database", {}).items():
        db_type = info["type"]
        id_key = database_id_key(db_type)
        remote: dict = client.get(database_endpoint(db_type, "one"), {id_key: info[id_key]})  # type: ignore[assignment]
        status = remote.get("applicationStatus", "unknown")
        print(f"  {name:10s}  {status}  ({db_type})")


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


def cmd_clean(cfg: dict, state_file: Path) -> None:
    """Remove stale Traefik configs and orphaned Docker services."""
    state = load_state(state_file)
    print("Cleaning stale routes...")
    cleanup_stale_routes(state, cfg)
    print("Clean complete.")


def cmd_destroy(client: DokployClient, cfg: dict, state_file: Path) -> None:
    state = load_state(state_file)

    cleanup_stale_routes(state, cfg)

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

    # Import registries referenced by config
    registries_cfg = cfg.get("registries", [])
    if registries_cfg:
        existing_registries = client.get("registry.all")
        existing_by_name = {r["registryName"]: r for r in existing_registries}
        state["registries"] = {}
        for reg_def in registries_cfg:
            name = reg_def["name"]
            if name in existing_by_name:
                state["registries"][name] = {"registryId": existing_by_name[name]["registryId"]}
                print(f"  Registry '{name}': {existing_by_name[name]['registryId']}")

    save_state(state, state_file)
    print("\nImport complete!")
    print(f"  Project: {project_id}")
    for name, info in state["apps"].items():
        rid = info.get("composeId") or info.get("applicationId")
        print(f"  {name}: {rid}")
