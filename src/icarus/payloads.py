from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icarus.client import DokployClient

DATABASE_TYPES = {"postgres", "mysql", "mariadb", "mongo", "redis"}

DATABASE_DEFAULTS = {
    "postgres": "postgres:16",
    "mysql": "mysql:8",
    "mariadb": "mariadb:11",
    "mongo": "mongo:7",
    "redis": "redis:7",
}


def database_endpoint(db_type: str, action: str) -> str:
    """Return the Dokploy API endpoint for a database operation."""
    return f"{db_type}.{action}"


def database_id_key(db_type: str) -> str:
    """Return the ID field name for a database type."""
    return f"{db_type}Id"


def build_database_create_payload(name: str, db_def: dict, environment_id: str) -> dict:
    """Build the API payload for creating a database resource."""
    db_type = db_def["type"]
    payload: dict = {
        "name": name,
        "environmentId": environment_id,
        "dockerImage": db_def.get("dockerImage", DATABASE_DEFAULTS[db_type]),
        "databasePassword": db_def["databasePassword"],
    }

    if db_def.get("description"):
        payload["description"] = db_def["description"]

    if db_type in ("postgres", "mysql", "mariadb"):
        payload["databaseName"] = db_def["databaseName"]
        payload["databaseUser"] = db_def["databaseUser"]

    if db_type in ("mysql", "mariadb"):
        payload["databaseRootPassword"] = db_def["databaseRootPassword"]

    if db_type == "mongo":
        payload["databaseUser"] = db_def["databaseUser"]

    return payload


def build_database_update_payload(db_id: str, db_type: str, db_def: dict, remote: dict) -> dict | None:
    """Build the API payload for `<type>.update`, diffing dockerImage/description against remote state.

    Returns None if no update is needed.
    """
    id_key = database_id_key(db_type)
    payload: dict = {id_key: db_id}
    changed = False

    desired_image = db_def.get("dockerImage", DATABASE_DEFAULTS[db_type])
    if remote.get("dockerImage") != desired_image:
        payload["dockerImage"] = desired_image
        changed = True

    if "description" in db_def and remote.get("description") != db_def["description"]:
        payload["description"] = db_def["description"]
        changed = True

    return payload if changed else None


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


def resolve_github_provider(client: DokployClient, providers: list[dict], owner: str) -> str:
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
        "dockerfile": app_def.get("dockerfile", "Dockerfile") if build_type == "dockerfile" else None,
        "dockerContextPath": app_def.get("dockerContextPath", ""),
        "dockerBuildStage": app_def.get("dockerBuildStage", ""),
        "herokuVersion": None,
        "railpackVersion": None,
    }
    if build_type == "static":
        payload["publishDirectory"] = app_def.get("publishDirectory", "")
        payload["isStaticSpa"] = app_def.get("isStaticSpa", False)
    return payload


def is_compose(app_def: dict) -> bool:
    """Check if an app definition uses compose source type."""
    return app_def.get("source") == "compose"


def resolve_compose_file(app_def: dict, repo_root: Path) -> str:
    """Resolve compose file content from inline block scalar or relative path."""
    compose_file = app_def["composeFile"]
    # Multi-line string = inline block scalar
    if "\n" in compose_file:
        return compose_file
    # Single-line = relative file path
    path = repo_root / compose_file
    if not path.exists():
        print(f"ERROR: Compose file not found: {path}")
        sys.exit(1)
    return path.read_text()


def build_compose_update_payload(
    compose_id: str,
    app_def: dict,
    github_cfg: dict | None,
    github_id: str | None,
    repo_root: Path | None,
) -> dict:
    """Build the source payload for compose.update.

    For sourceType: github, sends repo coordinates and composePath so Dokploy
    clones the repository server-side. For raw (default), sends the compose file
    content inline.
    """
    if app_def.get("sourceType") == "github":
        assert github_cfg is not None, "github_cfg required for sourceType: github"
        assert github_id is not None, "github_id required for sourceType: github"
        return {
            "composeId": compose_id,
            "sourceType": "github",
            "repository": github_cfg["repository"],
            "owner": github_cfg["owner"],
            "branch": github_cfg["branch"],
            "githubId": github_id,
            "composePath": app_def["composeFile"],
        }
    return {
        "composeId": compose_id,
        "sourceType": "raw",
        "composeFile": resolve_compose_file(app_def, repo_root),
    }


def build_domain_payload(resource_id: str, dom: dict, *, compose: bool = False) -> dict:
    """Build payload for domain.create."""
    if compose:
        payload = {
            "composeId": resource_id,
            "domainType": "compose",
            "serviceName": dom["serviceName"],
        }
    else:
        payload = {
            "applicationId": resource_id,
        }
    payload.update(
        {
            "host": dom["host"],
            "port": dom["port"],
            "https": dom["https"],
            "certificateType": dom["certificateType"],
        }
    )
    if dom.get("certificate"):
        payload["customCertResolver"] = dom["certificate"]
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


def build_mount_payload(app_id: str, mount: dict) -> dict:
    """Build payload for mounts.create."""
    payload = {
        "serviceId": app_id,
        "type": mount["type"],
        "mountPath": mount["target"],
        "serviceType": "application",
    }
    if mount["type"] == "volume":
        payload["volumeName"] = mount["source"]
    elif mount["type"] == "bind":
        payload["hostPath"] = mount["source"]
    return payload


def build_port_payload(app_id: str, port: dict) -> dict:
    """Build payload for port.create."""
    return {
        "applicationId": app_id,
        "publishedPort": port["publishedPort"],
        "targetPort": port["targetPort"],
        "protocol": port.get("protocol", "tcp"),
        "publishMode": port.get("publishMode", "ingress"),
    }


def build_registry_create_payload(reg_def: dict) -> dict:
    """Build the API payload for creating a container registry."""
    return {
        "registryName": reg_def["name"],
        "username": reg_def["username"],
        "password": reg_def["password"],
        "registryUrl": reg_def["registryUrl"],
        "registryType": "cloud",
        "imagePrefix": reg_def.get("imagePrefix"),
    }


def build_registry_update_payload(registry_id: str, reg_def: dict) -> dict:
    """Build the API payload for updating a container registry."""
    return {
        "registryId": registry_id,
        "registryName": reg_def["name"],
        "username": reg_def["username"],
        "password": reg_def["password"],
        "registryUrl": reg_def["registryUrl"],
        "registryType": "cloud",
        "imagePrefix": reg_def.get("imagePrefix"),
    }


def resolve_registry_id(state: dict, registry_name: str) -> str | None:
    """Look up registryId by name from state."""
    return state.get("registries", {}).get(registry_name, {}).get("registryId")


def build_security_payload(app_id: str, sec: dict) -> dict:
    """Build payload for security.create."""
    return {
        "applicationId": app_id,
        "username": sec["username"],
        "password": sec["password"],
    }


def build_redirect_payload(app_id: str, redirect: dict) -> dict:
    """Build payload for redirects.create."""
    return {
        "applicationId": app_id,
        "regex": redirect["regex"],
        "replacement": redirect["replacement"],
        "permanent": redirect["permanent"],
    }


def build_destination_create_payload(dest_def: dict) -> dict:
    """Build the API payload for creating a backup destination."""
    return {
        "name": dest_def["name"],
        "accessKey": dest_def["accessKey"],
        "secretAccessKey": dest_def["secretAccessKey"],
        "bucket": dest_def["bucket"],
        "region": dest_def["region"],
        "endpoint": dest_def["endpoint"],
    }


def build_destination_update_payload(destination_id: str, dest_def: dict) -> dict:
    """Build the API payload for updating a backup destination."""
    return {
        "destinationId": destination_id,
        **build_destination_create_payload(dest_def),
    }


def build_certificate_create_payload(cert_def: dict, organization_id: str, repo_root: Path) -> dict:
    """Build the API payload for creating a certificate."""
    cert_path = Path(cert_def["certFile"])
    key_path = Path(cert_def["keyFile"])
    if not cert_path.is_absolute():
        cert_path = repo_root / cert_path
    if not key_path.is_absolute():
        key_path = repo_root / key_path

    payload: dict = {
        "name": cert_def["name"],
        "certificateData": cert_path.read_text(),
        "privateKey": key_path.read_text(),
        "organizationId": organization_id,
    }
    if "autoRenew" in cert_def:
        payload["autoRenew"] = cert_def["autoRenew"]
    return payload


def build_backup_create_payload(
    backup_def: dict,
    *,
    db_id: str,
    db_type: str,
    db_name: str,
    destination_id: str,
) -> dict:
    """Build the API payload for creating a database backup schedule."""
    id_key = database_id_key(db_type)
    payload: dict = {
        "schedule": backup_def["schedule"],
        "prefix": backup_def["prefix"],
        "enabled": backup_def.get("enabled", True),
        "destinationId": destination_id,
        "databaseType": db_type,
        "database": db_name,
        "backupType": "database",
        id_key: db_id,
    }
    if "keepLatestCount" in backup_def:
        payload["keepLatestCount"] = backup_def["keepLatestCount"]
    return payload


def build_schedule_payload(app_id: str, sched: dict) -> dict:
    """Build payload for schedule.create."""
    payload = {
        "name": sched["name"],
        "cronExpression": sched["cronExpression"],
        "command": sched["command"],
        "scheduleType": "application",
        "applicationId": app_id,
        "shellType": sched.get("shellType", "bash"),
        "enabled": sched.get("enabled", True),
    }
    if "timezone" in sched:
        payload["timezone"] = sched["timezone"]
    return payload
