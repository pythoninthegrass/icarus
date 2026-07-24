from __future__ import annotations

import httpx
from icarus.client import save_state
from icarus.env import resolve_refs
from icarus.payloads import (
    build_app_settings_payload,
    build_backup_create_payload,
    build_certificate_create_payload,
    build_database_update_payload,
    build_destination_create_payload,
    build_destination_update_payload,
    build_domain_payload,
    build_mount_payload,
    build_port_payload,
    build_redirect_payload,
    build_registry_create_payload,
    build_registry_update_payload,
    build_schedule_payload,
    build_security_payload,
    build_volume_backup_payload,
    database_endpoint,
    database_id_key,
    is_compose,
    resolve_registry_id,
)
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icarus.client import DokployClient


def reconcile_schedules(
    client: DokployClient,
    app_id: str,
    existing: list[dict],
    desired: list[dict],
) -> dict:
    """Reconcile schedules: update existing by name, create new, delete removed.

    Returns a dict mapping schedule name -> {"scheduleId": ...} for state storage.
    """
    existing_by_name = {s["name"]: s for s in existing}
    desired_by_name = {s["name"]: s for s in desired}

    result_state = {}

    for name, sched in desired_by_name.items():
        payload = build_schedule_payload(app_id, sched)
        if name in existing_by_name:
            ex = existing_by_name[name]
            schedule_id = ex["scheduleId"]
            needs_update = (
                payload.get("cronExpression") != ex.get("cronExpression")
                or payload.get("command") != ex.get("command")
                or payload.get("shellType") != ex.get("shellType")
                or payload.get("enabled") != ex.get("enabled")
                or payload.get("timezone") != ex.get("timezone")
            )
            if needs_update:
                update_payload = {**payload, "scheduleId": schedule_id}
                update_payload.pop("applicationId", None)
                update_payload.pop("scheduleType", None)
                client.post("schedule.update", update_payload)
            result_state[name] = {"scheduleId": schedule_id}
        else:
            resp = client.post("schedule.create", payload)
            result_state[name] = {"scheduleId": resp["scheduleId"]}

    for name, ex in existing_by_name.items():
        if name not in desired_by_name:
            client.post("schedule.delete", {"scheduleId": ex["scheduleId"]})

    return result_state


def reconcile_app_schedules(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile schedules for all apps on redeploy."""
    changed = False
    for app_def in cfg.get("apps", []):
        schedules = app_def.get("schedules")
        if schedules is None and "schedules" not in state["apps"].get(app_def["name"], {}):
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]
        existing = client.get(
            "schedule.list",
            {"id": app_id, "scheduleType": "application"},
        )
        if not isinstance(existing, list):
            existing = []
        desired = schedules or []
        new_state = reconcile_schedules(client, app_id, existing, desired)
        state["apps"][name]["schedules"] = new_state
        changed = True
    if changed:
        save_state(state, state_file)


def reconcile_mounts(
    client: DokployClient,
    app_id: str,
    existing: list[dict],
    desired: list[dict],
) -> dict:
    """Reconcile mounts: update existing by mountPath, create new, delete removed.

    Returns a dict mapping mountPath -> {"mountId": ...} for state storage.
    """
    existing_by_path = {m["mountPath"]: m for m in existing}
    desired_by_path = {m["target"]: m for m in desired}

    result_state = {}

    for path, mount in desired_by_path.items():
        payload = build_mount_payload(app_id, mount)
        if path in existing_by_path:
            ex = existing_by_path[path]
            mount_id = ex["mountId"]
            needs_update = (
                payload.get("type") != ex.get("type")
                or payload.get("volumeName") != ex.get("volumeName")
                or payload.get("hostPath") != ex.get("hostPath")
            )
            if needs_update:
                update_payload = {**payload, "mountId": mount_id}
                update_payload.pop("serviceId", None)
                update_payload.pop("serviceType", None)
                client.post("mounts.update", update_payload)
            result_state[path] = {"mountId": mount_id}
        else:
            resp = client.post("mounts.create", payload)
            result_state[path] = {"mountId": resp["mountId"]}

    for path, ex in existing_by_path.items():
        if path not in desired_by_path:
            client.post("mounts.remove", {"mountId": ex["mountId"]})

    return result_state


def reconcile_ports(
    client: DokployClient,
    app_id: str,
    existing: list[dict],
    desired: list[dict],
) -> dict:
    """Reconcile ports: update existing by publishedPort, create new, delete removed.

    Returns a dict mapping publishedPort -> {"portId": ...} for state storage.
    """
    existing_by_port = {p["publishedPort"]: p for p in existing}
    desired_by_port = {p["publishedPort"]: p for p in desired}

    result_state = {}

    for pub_port, port in desired_by_port.items():
        payload = build_port_payload(app_id, port)
        if pub_port in existing_by_port:
            ex = existing_by_port[pub_port]
            port_id = ex["portId"]
            needs_update = any(payload.get(key) != ex.get(key) for key in ("targetPort", "protocol", "publishMode"))
            if needs_update:
                update_payload = {
                    "portId": port_id,
                    "publishedPort": payload["publishedPort"],
                    "targetPort": payload["targetPort"],
                    "protocol": payload["protocol"],
                    "publishMode": payload["publishMode"],
                }
                client.post("port.update", update_payload)
            result_state[pub_port] = {"portId": port_id}
        else:
            resp = client.post("port.create", payload)
            result_state[pub_port] = {"portId": resp["portId"]}

    for pub_port, ex in existing_by_port.items():
        if pub_port not in desired_by_port:
            client.post("port.delete", {"portId": ex["portId"]})

    return result_state


def reconcile_app_ports(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile ports for all apps on redeploy."""
    changed = False
    for app_def in cfg.get("apps", []):
        if is_compose(app_def):
            continue
        ports = app_def.get("ports")
        name = app_def["name"]
        if ports is None and "ports" not in state["apps"].get(name, {}):
            continue
        app_id = state["apps"][name]["applicationId"]
        remote = client.get("application.one", {"applicationId": app_id})
        existing = remote.get("ports") or []
        desired = ports or []
        new_state = reconcile_ports(client, app_id, existing, desired)
        state["apps"][name]["ports"] = new_state
        changed = True
    if changed:
        save_state(state, state_file)


def reconcile_redirects(
    client: DokployClient,
    app_id: str,
    existing: list[dict],
    desired: list[dict],
) -> dict:
    """Reconcile redirects: update existing by regex, create new, delete removed.

    Returns a dict mapping regex -> {"redirectId": ...} for state storage.
    """
    existing_by_regex = {r["regex"]: r for r in existing}
    desired_by_regex = {r["regex"]: r for r in desired}

    result_state = {}

    for regex, redir in desired_by_regex.items():
        payload = build_redirect_payload(app_id, redir)
        if regex in existing_by_regex:
            ex = existing_by_regex[regex]
            redirect_id = ex["redirectId"]
            needs_update = any(payload.get(key) != ex.get(key) for key in ("replacement", "permanent"))
            if needs_update:
                update_payload = {
                    "redirectId": redirect_id,
                    "regex": payload["regex"],
                    "replacement": payload["replacement"],
                    "permanent": payload["permanent"],
                }
                client.post("redirects.update", update_payload)
            result_state[regex] = {"redirectId": redirect_id}
        else:
            resp = client.post("redirects.create", payload)
            result_state[regex] = {"redirectId": resp["redirectId"]}

    for regex, ex in existing_by_regex.items():
        if regex not in desired_by_regex:
            client.post("redirects.delete", {"redirectId": ex["redirectId"]})

    return result_state


def reconcile_app_redirects(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile redirects for all apps on redeploy."""
    changed = False
    for app_def in cfg.get("apps", []):
        if is_compose(app_def):
            continue
        redirects = app_def.get("redirects")
        name = app_def["name"]
        if redirects is None and "redirects" not in state["apps"].get(name, {}):
            continue
        app_id = state["apps"][name]["applicationId"]
        remote = client.get("application.one", {"applicationId": app_id})
        existing = remote.get("redirects") or []
        desired = redirects or []
        new_state = reconcile_redirects(client, app_id, existing, desired)
        state["apps"][name]["redirects"] = new_state
        changed = True
    if changed:
        save_state(state, state_file)


def reconcile_app_mounts(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile mounts for all apps on redeploy."""
    changed = False
    for app_def in cfg.get("apps", []):
        if is_compose(app_def):
            continue
        volumes = app_def.get("volumes")
        name = app_def["name"]
        if volumes is None and "mounts" not in state["apps"].get(name, {}):
            continue
        app_id = state["apps"][name]["applicationId"]
        remote = client.get("application.one", {"applicationId": app_id})
        existing = remote.get("mounts") or []
        desired = volumes or []
        new_state = reconcile_mounts(client, app_id, existing, desired)
        state["apps"][name]["mounts"] = new_state
        changed = True
    if changed:
        save_state(state, state_file)


def _domain_key(d: dict) -> tuple[str, str]:
    """Return (host, path) identifying a domain entry uniquely."""
    return (d["host"], d.get("path") or "/")


def _domain_state_key(host: str, path: str) -> str:
    """State dict key for a domain.

    The root path is stored under the bare host to preserve compatibility with
    existing state files. Non-root paths use ``host/path``.
    """
    if not path or path == "/":
        return host
    return f"{host}{path}"


def reconcile_domains(
    client: DokployClient,
    resource_id: str,
    existing: list[dict],
    desired: list[dict],
    *,
    compose: bool = False,
) -> dict:
    """Reconcile domains: update existing by (host, path), create new, delete removed.

    Returns a dict mapping state key -> {"domainId": ...} for state storage.
    """
    existing_by_key = {_domain_key(d): d for d in existing}
    desired_by_key = {_domain_key(d): d for d in desired}

    result_state = {}

    for key, dom in desired_by_key.items():
        host, path = key
        state_key = _domain_state_key(host, path)
        payload = build_domain_payload(resource_id, dom, compose=compose)
        if key in existing_by_key:
            ex = existing_by_key[key]
            domain_id = ex["domainId"]
            needs_update = any(
                payload.get(k) != ex.get(k)
                for k in ("port", "https", "certificateType", "customCertResolver", "path", "internalPath", "stripPath")
            )
            if needs_update:
                update_payload = {k: v for k, v in payload.items() if k not in ("applicationId", "composeId", "domainType")}
                update_payload["domainId"] = domain_id
                client.post("domain.update", update_payload)
            result_state[state_key] = {"domainId": domain_id}
        else:
            resp = client.post("domain.create", payload)
            result_state[state_key] = {"domainId": resp["domainId"]}

    for key, ex in existing_by_key.items():
        if key not in desired_by_key:
            client.post("domain.delete", {"domainId": ex["domainId"]})

    return result_state


def reconcile_app_domains(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile domains for all apps on redeploy."""
    changed = False
    for app_def in cfg.get("apps", []):
        domain_cfg = app_def.get("domain")
        name = app_def["name"]
        if domain_cfg is None and "domains" not in state["apps"].get(name, {}):
            continue
        compose = is_compose(app_def)
        if compose:
            resource_id = state["apps"][name]["composeId"]
            existing = client.get("domain.byComposeId", {"composeId": resource_id})
        else:
            resource_id = state["apps"][name]["applicationId"]
            existing = client.get("domain.byApplicationId", {"applicationId": resource_id})
        if not isinstance(existing, list):
            existing = []
        desired = domain_cfg if isinstance(domain_cfg, list) else [domain_cfg] if domain_cfg else []
        new_state = reconcile_domains(client, resource_id, existing, desired, compose=compose)
        state["apps"][name]["domains"] = new_state
        changed = True
    if changed:
        save_state(state, state_file)


def reconcile_app_settings(
    client: DokployClient,
    cfg: dict,
    state: dict,
) -> None:
    """Reconcile app settings (command, replicas, autoDeploy, resources,
    healthCheck, restartPolicy) on redeploy."""
    for app_def in cfg.get("apps", []):
        if is_compose(app_def):
            continue
        name = app_def["name"]
        app_id = state["apps"][name]["applicationId"]

        settings_payload = build_app_settings_payload(app_id, app_def)
        command = app_def.get("command")
        has_settings = settings_payload is not None or command is not None
        if not has_settings:
            continue

        remote = client.get("application.one", {"applicationId": app_id})

        update_payload: dict = {"applicationId": app_id}
        changed = False

        if command is not None and remote.get("command") != command:
            resolved = resolve_refs(command, state)
            update_payload["command"] = resolved
            changed = True

        for key, value in (settings_payload or {}).items():
            if key != "applicationId" and remote.get(key) != value:
                update_payload[key] = value
                changed = True

        if changed:
            client.post("application.update", update_payload)


def reconcile_registries(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile registries: create missing, update existing credentials."""
    registries_cfg = cfg.get("registries", [])
    if not registries_cfg:
        return

    existing_registries = client.get("registry.all")
    existing_by_name = {r["registryName"]: r for r in existing_registries}

    if "registries" not in state:
        state["registries"] = {}

    for reg_def in registries_cfg:
        name = reg_def["name"]
        if name in existing_by_name:
            registry_id = existing_by_name[name]["registryId"]
            update_payload = build_registry_update_payload(registry_id, reg_def)
            client.post("registry.update", update_payload)
        else:
            payload = build_registry_create_payload(reg_def)
            resp = client.post("registry.create", payload)
            registry_id = resp["registryId"]
        state["registries"][name] = {"registryId": registry_id}

        try:
            client.post("registry.testRegistry", build_registry_create_payload(reg_def))
        except httpx.HTTPStatusError as exc:
            raise SystemExit(f"ERROR: Registry connection test failed for '{name}': {exc}") from exc

    save_state(state, state_file)


def reconcile_app_registry(
    client: DokployClient,
    cfg: dict,
    state: dict,
) -> None:
    """Reconcile app-to-registry associations on redeploy."""
    for app_def in cfg.get("apps", []):
        if is_compose(app_def):
            continue
        name = app_def["name"]
        app_info = state["apps"].get(name)
        if not app_info or "applicationId" not in app_info:
            continue

        app_id = app_info["applicationId"]
        registry_name = app_def.get("registry")
        desired_registry_id = resolve_registry_id(state, registry_name) if registry_name else None

        remote = client.get("application.one", {"applicationId": app_id})
        current_registry_id = remote.get("registryId")

        if current_registry_id != desired_registry_id:
            client.post("application.update", {"applicationId": app_id, "registryId": desired_registry_id})


def reconcile_security(
    client: DokployClient,
    app_id: str,
    existing: list[dict],
    desired: list[dict],
) -> dict:
    """Reconcile security: update existing by username, create new, delete removed.

    Returns a dict mapping username -> {"securityId": ...} for state storage.
    """
    existing_by_user = {s["username"]: s for s in existing}
    desired_by_user = {s["username"]: s for s in desired}

    result_state = {}

    for username, sec in desired_by_user.items():
        payload = build_security_payload(app_id, sec)
        if username in existing_by_user:
            ex = existing_by_user[username]
            security_id = ex["securityId"]
            needs_update = payload.get("password") != ex.get("password")
            if needs_update:
                update_payload = {
                    "securityId": security_id,
                    "username": payload["username"],
                    "password": payload["password"],
                }
                client.post("security.update", update_payload)
            result_state[username] = {"securityId": security_id}
        else:
            resp = client.post("security.create", payload)
            result_state[username] = {"securityId": resp["securityId"]}

    for username, ex in existing_by_user.items():
        if username not in desired_by_user:
            client.post("security.delete", {"securityId": ex["securityId"]})

    return result_state


def reconcile_app_security(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile security for all apps on redeploy."""
    changed = False
    for app_def in cfg.get("apps", []):
        if is_compose(app_def):
            continue
        security = app_def.get("security")
        name = app_def["name"]
        if security is None and "security" not in state["apps"].get(name, {}):
            continue
        app_id = state["apps"][name]["applicationId"]
        remote = client.get("application.one", {"applicationId": app_id})
        existing = remote.get("security") or []
        desired = security or []
        new_state = reconcile_security(client, app_id, existing, desired)
        state["apps"][name]["security"] = new_state
        changed = True
    if changed:
        save_state(state, state_file)


def reconcile_destinations(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile backup destinations: create missing, update existing."""
    destinations_cfg = cfg.get("destinations", [])
    if not destinations_cfg:
        return

    existing_destinations = client.get("destination.all")
    existing_by_name = {d["name"]: d for d in existing_destinations}

    if "destinations" not in state:
        state["destinations"] = {}

    for dest_def in destinations_cfg:
        name = dest_def["name"]
        if name in existing_by_name:
            destination_id = existing_by_name[name]["destinationId"]
            update_payload = build_destination_update_payload(destination_id, dest_def)
            client.post("destination.update", update_payload)
        else:
            payload = build_destination_create_payload(dest_def)
            resp = client.post("destination.create", payload)
            destination_id = resp["destinationId"]
        state["destinations"][name] = {"destinationId": destination_id}

        try:
            client.post("destination.testConnection", build_destination_create_payload(dest_def))
        except httpx.HTTPStatusError as exc:
            raise SystemExit(f"ERROR: Destination connection test failed for '{name}': {exc}") from exc

    save_state(state, state_file)


def reconcile_certificates(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
    *,
    repo_root: Path | None = None,
) -> None:
    """Reconcile certificates: create missing, update existing (none), delete removed."""
    certificates_cfg = cfg.get("certificates", [])
    existing_state = state.get("certificates", {})
    if not certificates_cfg and not existing_state:
        return

    existing_certs = client.get("certificates.all")
    existing_by_name = {c["name"]: c for c in existing_certs}

    if "certificates" not in state:
        state["certificates"] = {}

    organization_id = state.get("organizationId", "")
    effective_root = repo_root or Path(".")

    desired_names = set()
    for cert_def in certificates_cfg:
        name = cert_def["name"]
        desired_names.add(name)
        if name in existing_by_name:
            certificate_id = existing_by_name[name]["certificateId"]
        else:
            payload = build_certificate_create_payload(cert_def, organization_id, effective_root)
            resp = client.post("certificates.create", payload)
            certificate_id = resp["certificateId"]
        state["certificates"][name] = {"certificateId": certificate_id}

    for name in list(state["certificates"]):
        if name not in desired_names:
            certificate_id = state["certificates"][name]["certificateId"]
            client.post("certificates.remove", {"certificateId": certificate_id})
            del state["certificates"][name]

    save_state(state, state_file)


def reconcile_databases(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile database drift (image, description) and rebuild on image change."""
    for db_def in cfg.get("database", []):
        name = db_def["name"]
        db_type = db_def["type"]
        id_key = database_id_key(db_type)
        db_info = state.get("database", {}).get(name)
        if not db_info:
            continue
        db_id = db_info[id_key]
        remote = client.get(database_endpoint(db_type, "one"), {id_key: db_id})
        update_payload = build_database_update_payload(db_id, db_type, db_def, remote)
        if update_payload is None:
            continue
        client.post(database_endpoint(db_type, "update"), update_payload)
        if "dockerImage" in update_payload:
            client.post(database_endpoint(db_type, "rebuild"), {id_key: db_id})


def reconcile_database_backups(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile backup schedules for all databases."""
    changed = False
    for db_def in cfg.get("database", []):
        backups_cfg = db_def.get("backups")
        name = db_def["name"]
        db_type = db_def["type"]
        if backups_cfg is None and "backups" not in state.get("database", {}).get(name, {}):
            continue

        db_info = state["database"][name]
        id_key = database_id_key(db_type)
        db_id = db_info[id_key]

        existing = client.get("backup.all", {id_key: db_id})
        if not isinstance(existing, list):
            existing = []

        desired = backups_cfg or []
        existing_by_prefix = {b["prefix"]: b for b in existing}
        desired_by_prefix = {b["prefix"]: b for b in desired}

        result_state = {}
        for prefix, backup_def in desired_by_prefix.items():
            destination_id = state.get("destinations", {}).get(backup_def["destination"], {}).get("destinationId")
            if prefix in existing_by_prefix:
                ex = existing_by_prefix[prefix]
                backup_id = ex["backupId"]
                payload = build_backup_create_payload(
                    backup_def,
                    db_id=db_id,
                    db_type=db_type,
                    db_name=name,
                    destination_id=destination_id,
                )
                needs_update = any(
                    payload.get(k) != ex.get(k) for k in ("schedule", "enabled", "keepLatestCount", "destinationId")
                )
                if needs_update:
                    update_payload = {**payload, "backupId": backup_id}
                    client.post("backup.update", update_payload)
                result_state[prefix] = {"backupId": backup_id, "destinationId": destination_id}
            else:
                payload = build_backup_create_payload(
                    backup_def,
                    db_id=db_id,
                    db_type=db_type,
                    db_name=name,
                    destination_id=destination_id,
                )
                resp = client.post("backup.create", payload)
                result_state[prefix] = {"backupId": resp["backupId"], "destinationId": destination_id}

        for prefix, ex in existing_by_prefix.items():
            if prefix not in desired_by_prefix:
                client.post("backup.remove", {"backupId": ex["backupId"]})

        state["database"][name]["backups"] = result_state
        changed = True

    if changed:
        save_state(state, state_file)


def _reconcile_volume_backups_for_service(
    client: DokployClient,
    cfg_entries: list,
    state: dict,
    service_type: str,
    service_id_key: str,
    service_id: str,
) -> dict:
    """Reconcile volume backups for a single app/database against desired config, by name."""
    existing = client.get("volumeBackups.list", {"id": service_id, "volumeBackupType": service_type})
    if not isinstance(existing, list):
        existing = []

    desired_by_name = {vb["name"]: vb for vb in cfg_entries}
    existing_by_name = {vb["name"]: vb for vb in existing}

    result_state = {}
    for vb_name, vb_def in desired_by_name.items():
        destination_id = state.get("destinations", {}).get(vb_def["destination"], {}).get("destinationId")
        payload = build_volume_backup_payload(
            vb_def,
            destination_id=destination_id,
            service_type=service_type,
            service_id_key=service_id_key,
            service_id=service_id,
        )
        if vb_name in existing_by_name:
            ex = existing_by_name[vb_name]
            volume_backup_id = ex["volumeBackupId"]
            needs_update = any(
                payload.get(k) != ex.get(k)
                for k in ("volumeName", "prefix", "cronExpression", "enabled", "keepLatestCount", "destinationId")
            )
            if needs_update:
                update_payload = {**payload, "volumeBackupId": volume_backup_id}
                client.post("volumeBackups.update", update_payload)
            result_state[vb_name] = {"volumeBackupId": volume_backup_id}
        else:
            resp = client.post("volumeBackups.create", payload)
            result_state[vb_name] = {"volumeBackupId": resp["volumeBackupId"]}

    for vb_name, ex in existing_by_name.items():
        if vb_name not in desired_by_name:
            client.post("volumeBackups.delete", {"volumeBackupId": ex["volumeBackupId"]})

    return result_state


def reconcile_volume_backups(
    client: DokployClient,
    cfg: dict,
    state: dict,
    state_file: Path,
) -> None:
    """Reconcile volumeBackups for all apps and databases."""
    changed = False

    for app_def in cfg.get("apps", []):
        vb_cfg = app_def.get("volumeBackups")
        name = app_def["name"]
        app_info = state["apps"].get(name, {})
        if vb_cfg is None and "volumeBackups" not in app_info:
            continue
        compose = is_compose(app_def)
        service_type = "compose" if compose else "application"
        service_id_key = "composeId" if compose else "applicationId"
        service_id = app_info[service_id_key]
        state["apps"][name]["volumeBackups"] = _reconcile_volume_backups_for_service(
            client, vb_cfg or [], state, service_type, service_id_key, service_id
        )
        changed = True

    for db_def in cfg.get("database", []):
        vb_cfg = db_def.get("volumeBackups")
        name = db_def["name"]
        db_info = state.get("database", {}).get(name, {})
        if vb_cfg is None and "volumeBackups" not in db_info:
            continue
        db_type = db_def["type"]
        service_id_key = database_id_key(db_type)
        service_id = db_info[service_id_key]
        state["database"][name]["volumeBackups"] = _reconcile_volume_backups_for_service(
            client, vb_cfg or [], state, db_type, service_id_key, service_id
        )
        changed = True

    if changed:
        save_state(state, state_file)
