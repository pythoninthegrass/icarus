from __future__ import annotations

from icarus.client import load_state, validate_state
from icarus.env import filter_env, get_env_excludes, resolve_refs
from icarus.payloads import (
    DATABASE_DEFAULTS,
    build_mount_payload,
    build_port_payload,
    build_redirect_payload,
    build_schedule_payload,
    build_security_payload,
    is_compose,
)
from icarus.reconcile import _domain_key
from icarus.schema import get_state_file
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icarus.client import DokployClient


def _env_keys(env_blob: str | None) -> set[str]:
    """Extract variable names from a KEY=value env blob."""
    if not env_blob:
        return set()
    keys = set()
    for line in env_blob.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            keys.add(stripped.split("=", 1)[0].strip())
    return keys


def _plan_initial_setup(cfg: dict, repo_root: Path, changes: list[dict]) -> None:
    """Populate changes list for a fresh setup (no state exists)."""
    project_cfg = cfg["project"]
    changes.append(
        {
            "action": "create",
            "resource_type": "project",
            "name": project_cfg["name"],
            "parent": None,
            "attrs": {
                "name": project_cfg["name"],
                "description": project_cfg.get("description", ""),
            },
        }
    )

    for reg_def in cfg.get("registries", []):
        changes.append(
            {
                "action": "create",
                "resource_type": "registry",
                "name": reg_def["name"],
                "parent": None,
                "attrs": {
                    "registryUrl": reg_def["registryUrl"],
                    "imagePrefix": reg_def.get("imagePrefix"),
                },
            }
        )

    for app_def in cfg["apps"]:
        name = app_def["name"]
        compose = is_compose(app_def)
        rtype = "compose" if compose else "application"
        attrs: dict = {"source": app_def.get("source", "compose")}
        if not compose:
            if app_def.get("source") == "docker":
                attrs["dockerImage"] = app_def.get("dockerImage", "")
            elif app_def.get("source") == "github":
                attrs["buildType"] = app_def.get("buildType", "dockerfile")
            if app_def.get("registry"):
                attrs["registry"] = app_def["registry"]
        changes.append(
            {
                "action": "create",
                "resource_type": rtype,
                "name": name,
                "parent": None,
                "attrs": attrs,
            }
        )

        domain_cfg = app_def.get("domain")
        if domain_cfg:
            domains = domain_cfg if isinstance(domain_cfg, list) else [domain_cfg]
            for dom in domains:
                dom_attrs: dict = {
                    "host": dom["host"],
                    "port": dom["port"],
                    "https": dom.get("https", False),
                    "certificateType": dom.get("certificateType", "none"),
                }
                if dom.get("certificate"):
                    dom_attrs["certificate"] = dom["certificate"]
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "domain",
                        "name": dom["host"],
                        "parent": name,
                        "attrs": dom_attrs,
                    }
                )

        if not compose:
            settings_keys = {}
            for key in ("autoDeploy", "replicas"):
                if key in app_def:
                    settings_keys[key] = app_def[key]
            if settings_keys:
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "settings",
                        "name": name,
                        "parent": None,
                        "attrs": settings_keys,
                    }
                )

            for vol in app_def.get("volumes", []):
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "mount",
                        "name": f"{vol['source']} -> {vol['target']}",
                        "parent": name,
                        "attrs": {
                            "type": vol["type"],
                            "source": vol["source"],
                            "target": vol["target"],
                        },
                    }
                )

            for port in app_def.get("ports", []):
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "port",
                        "name": f"{port['publishedPort']} -> {port['targetPort']}",
                        "parent": name,
                        "attrs": {
                            "publishedPort": port["publishedPort"],
                            "targetPort": port["targetPort"],
                            "protocol": port.get("protocol", "tcp"),
                            "publishMode": port.get("publishMode", "ingress"),
                        },
                    }
                )

            for sched in app_def.get("schedules", []):
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "schedule",
                        "name": sched["name"],
                        "parent": name,
                        "attrs": {
                            "cronExpression": sched["cronExpression"],
                            "command": sched["command"],
                        },
                    }
                )

            for sec in app_def.get("security", []):
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "security",
                        "name": sec["username"],
                        "parent": name,
                        "attrs": {
                            "username": sec["username"],
                        },
                    }
                )

            for redir in app_def.get("redirects", []):
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "redirect",
                        "name": redir["regex"],
                        "parent": name,
                        "attrs": {
                            "regex": redir["regex"],
                            "replacement": redir["replacement"],
                            "permanent": redir["permanent"],
                        },
                    }
                )

        for vb_def in app_def.get("volumeBackups", []):
            changes.append(
                {
                    "action": "create",
                    "resource_type": "volume_backup",
                    "name": vb_def["name"],
                    "parent": name,
                    "attrs": {
                        "volumeName": vb_def["volumeName"],
                        "cronExpression": vb_def["cronExpression"],
                        "destination": vb_def["destination"],
                        "keepLatestCount": vb_def.get("keepLatestCount"),
                    },
                }
            )

    env_targets = cfg["project"].get("env_targets", [])
    if env_targets:
        env_file = repo_root / ".env"
        if env_file.exists():
            raw_env = env_file.read_text()
            exclude_prefixes = get_env_excludes()
            filtered = filter_env(raw_env, exclude_prefixes)
            keys = _env_keys(filtered)
            for target_name in env_targets:
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "environment",
                        "name": target_name,
                        "parent": None,
                        "attrs": {"keys": sorted(keys)},
                    }
                )

    for app_def in cfg["apps"]:
        custom_env = app_def.get("env")
        if custom_env:
            keys = _env_keys(custom_env)
            changes.append(
                {
                    "action": "create",
                    "resource_type": "environment",
                    "name": app_def["name"],
                    "parent": None,
                    "attrs": {"keys": sorted(keys)},
                }
            )

    for dest_def in cfg.get("destinations", []):
        changes.append(
            {
                "action": "create",
                "resource_type": "destination",
                "name": dest_def["name"],
                "parent": None,
                "attrs": {
                    "bucket": dest_def["bucket"],
                    "region": dest_def["region"],
                    "endpoint": dest_def["endpoint"],
                },
            }
        )

    for cert_def in cfg.get("certificates", []):
        changes.append(
            {
                "action": "create",
                "resource_type": "certificate",
                "name": cert_def["name"],
                "parent": None,
                "attrs": {
                    "certFile": cert_def["certFile"],
                    "keyFile": cert_def["keyFile"],
                    "autoRenew": cert_def.get("autoRenew"),
                },
            }
        )

    for db_def in cfg.get("database", []):
        changes.append(
            {
                "action": "create",
                "resource_type": "database",
                "name": db_def["name"],
                "parent": None,
                "attrs": {
                    "type": db_def["type"],
                    "dockerImage": db_def.get("dockerImage", DATABASE_DEFAULTS[db_def["type"]]),
                },
            }
        )

        for backup_def in db_def.get("backups", []):
            changes.append(
                {
                    "action": "create",
                    "resource_type": "backup",
                    "name": backup_def["prefix"],
                    "parent": db_def["name"],
                    "attrs": {
                        "schedule": backup_def["schedule"],
                        "destination": backup_def["destination"],
                        "keepLatestCount": backup_def.get("keepLatestCount"),
                    },
                }
            )

        for vb_def in db_def.get("volumeBackups", []):
            changes.append(
                {
                    "action": "create",
                    "resource_type": "volume_backup",
                    "name": vb_def["name"],
                    "parent": db_def["name"],
                    "attrs": {
                        "volumeName": vb_def["volumeName"],
                        "cronExpression": vb_def["cronExpression"],
                        "destination": vb_def["destination"],
                        "keepLatestCount": vb_def.get("keepLatestCount"),
                    },
                }
            )


def _plan_redeploy(
    client: DokployClient,
    cfg: dict,
    state: dict,
    repo_root: Path,
    changes: list[dict],
) -> None:
    """Populate changes list by diffing remote state against desired config."""
    apps_by_name = {a["name"]: a for a in cfg["apps"]}
    env_targets = cfg["project"].get("env_targets", [])

    env_file = repo_root / ".env"
    filtered_env: str | None = None
    if env_targets and env_file.exists():
        raw_env = env_file.read_text()
        exclude_prefixes = get_env_excludes()
        filtered_env = filter_env(raw_env, exclude_prefixes)

    for name, app_info in state["apps"].items():
        app_def = apps_by_name.get(name)
        if app_def is None:
            continue
        compose = app_info.get("source") == "compose"

        if compose:
            remote = client.get("compose.one", {"composeId": app_info["composeId"]})
        else:
            remote = client.get(
                "application.one",
                {"applicationId": app_info["applicationId"]},
            )

        remote_env = remote.get("env") or ""

        desired_parts: list[str] = []
        if name in env_targets and filtered_env:
            resolved = resolve_refs(filtered_env, state)
            desired_parts.append(resolved)
        custom_env = apps_by_name.get(name, {}).get("env")
        if custom_env:
            resolved_custom = resolve_refs(custom_env, state)
            desired_parts.append(resolved_custom)

        desired_env = "\n".join(p.rstrip("\n") for p in desired_parts) + "\n" if desired_parts else ""

        remote_keys = _env_keys(remote_env)
        desired_keys = _env_keys(desired_env)

        if remote_keys != desired_keys:
            added = sorted(desired_keys - remote_keys)
            removed = sorted(remote_keys - desired_keys)
            changes.append(
                {
                    "action": "update",
                    "resource_type": "environment",
                    "name": name,
                    "parent": None,
                    "attrs": {"added": added, "removed": removed},
                }
            )

        domain_cfg = app_def.get("domain")
        if domain_cfg is not None or "domains" in state["apps"].get(name, {}):
            if compose:
                remote_domains = client.get("domain.byComposeId", {"composeId": app_info["composeId"]})
            else:
                remote_domains = client.get("domain.byApplicationId", {"applicationId": app_info["applicationId"]})
            if not isinstance(remote_domains, list):
                remote_domains = []

            desired_domains = domain_cfg if isinstance(domain_cfg, list) else [domain_cfg] if domain_cfg else []
            existing_by_key = {_domain_key(d): d for d in remote_domains}
            desired_by_key = {_domain_key(d): d for d in desired_domains}

            for (host, path), dom in desired_by_key.items():
                label = host if path == "/" else f"{host}{path}"
                if (host, path) in existing_by_key:
                    ex = existing_by_key[(host, path)]
                    diffs: dict = {}
                    for key in ("port", "https", "certificateType", "path", "internalPath", "stripPath"):
                        old_val = ex.get(key)
                        new_val = dom.get(key)
                        if old_val != new_val:
                            diffs[key] = (old_val, new_val)
                    if diffs:
                        changes.append(
                            {
                                "action": "update",
                                "resource_type": "domain",
                                "name": label,
                                "parent": name,
                                "attrs": diffs,
                            }
                        )
                else:
                    changes.append(
                        {
                            "action": "create",
                            "resource_type": "domain",
                            "name": label,
                            "parent": name,
                            "attrs": {
                                "host": host,
                                "port": dom.get("port"),
                                "https": dom.get("https", False),
                                "certificateType": dom.get("certificateType", "none"),
                            },
                        }
                    )

            for host, path in existing_by_key:
                if (host, path) not in desired_by_key:
                    label = host if path == "/" else f"{host}{path}"
                    changes.append(
                        {
                            "action": "destroy",
                            "resource_type": "domain",
                            "name": label,
                            "parent": name,
                            "attrs": {"host": host},
                        }
                    )

        if compose or app_def is None:
            continue

        settings_diffs: dict = {}
        for key in ("command", "replicas", "autoDeploy"):
            if key not in app_def:
                continue
            desired_val = app_def[key]
            remote_val = remote.get(key)
            if remote_val != desired_val:
                settings_diffs[key] = (remote_val, desired_val)
        if settings_diffs:
            changes.append(
                {
                    "action": "update",
                    "resource_type": "settings",
                    "name": name,
                    "parent": None,
                    "attrs": settings_diffs,
                }
            )

        volumes = app_def.get("volumes")
        if volumes is not None or "mounts" in state["apps"].get(name, {}):
            remote_mounts = remote.get("mounts") or []

            desired_mounts = volumes or []
            existing_by_path = {m["mountPath"]: m for m in remote_mounts}
            desired_by_path = {m["target"]: m for m in desired_mounts}

            for target, mount in desired_by_path.items():
                source = mount["source"]
                display_name = f"{source} -> {target}"
                if target in existing_by_path:
                    ex = existing_by_path[target]
                    payload = build_mount_payload(app_info["applicationId"], mount)
                    diffs: dict = {}
                    for key in ("type", "volumeName", "hostPath"):
                        old_val = ex.get(key)
                        new_val = payload.get(key)
                        if old_val != new_val:
                            diffs[key] = (old_val, new_val)
                    if diffs:
                        changes.append(
                            {
                                "action": "update",
                                "resource_type": "mount",
                                "name": display_name,
                                "parent": name,
                                "attrs": diffs,
                            }
                        )
                else:
                    changes.append(
                        {
                            "action": "create",
                            "resource_type": "mount",
                            "name": display_name,
                            "parent": name,
                            "attrs": {
                                "type": mount["type"],
                                "source": source,
                                "target": target,
                            },
                        }
                    )

            for target, ex in existing_by_path.items():
                if target not in desired_by_path:
                    ex_source = ex.get("hostPath") or ex.get("volumeName") or ""
                    changes.append(
                        {
                            "action": "destroy",
                            "resource_type": "mount",
                            "name": f"{ex_source} -> {target}",
                            "parent": name,
                            "attrs": {"mountPath": target},
                        }
                    )

        ports_cfg = app_def.get("ports")
        if ports_cfg is not None or "ports" in state["apps"].get(name, {}):
            remote_ports = remote.get("ports") or []

            desired_ports = ports_cfg or []
            existing_by_pub = {p["publishedPort"]: p for p in remote_ports}
            desired_by_pub = {p["publishedPort"]: p for p in desired_ports}

            for pub_port, port in desired_by_pub.items():
                payload = build_port_payload(app_info["applicationId"], port)
                display_name = f"{pub_port} -> {port['targetPort']}"
                if pub_port in existing_by_pub:
                    ex = existing_by_pub[pub_port]
                    diffs: dict = {}
                    for key in ("targetPort", "protocol", "publishMode"):
                        old_val = ex.get(key)
                        new_val = payload.get(key)
                        if old_val != new_val:
                            diffs[key] = (old_val, new_val)
                    if diffs:
                        changes.append(
                            {
                                "action": "update",
                                "resource_type": "port",
                                "name": display_name,
                                "parent": name,
                                "attrs": diffs,
                            }
                        )
                else:
                    changes.append(
                        {
                            "action": "create",
                            "resource_type": "port",
                            "name": display_name,
                            "parent": name,
                            "attrs": {
                                "publishedPort": pub_port,
                                "targetPort": port.get("targetPort"),
                                "protocol": port.get("protocol", "tcp"),
                                "publishMode": port.get("publishMode", "ingress"),
                            },
                        }
                    )

            for pub_port, ex in existing_by_pub.items():
                if pub_port not in desired_by_pub:
                    changes.append(
                        {
                            "action": "destroy",
                            "resource_type": "port",
                            "name": f"{pub_port} -> {ex.get('targetPort', '?')}",
                            "parent": name,
                            "attrs": {"publishedPort": pub_port},
                        }
                    )

        security_cfg = app_def.get("security")
        if security_cfg is not None or "security" in state["apps"].get(name, {}):
            remote_security = remote.get("security") or []
            desired_security = security_cfg or []

            existing_by_user = {s["username"]: s for s in remote_security}
            desired_by_user = {s["username"]: s for s in desired_security}

            for username, sec in desired_by_user.items():
                if username in existing_by_user:
                    ex = existing_by_user[username]
                    diffs: dict = {}
                    if sec.get("password") != ex.get("password"):
                        diffs["password"] = (ex.get("password"), sec.get("password"))
                    if diffs:
                        changes.append(
                            {
                                "action": "update",
                                "resource_type": "security",
                                "name": username,
                                "parent": name,
                                "attrs": diffs,
                            }
                        )
                else:
                    changes.append(
                        {
                            "action": "create",
                            "resource_type": "security",
                            "name": username,
                            "parent": name,
                            "attrs": {"username": username},
                        }
                    )

            for username in existing_by_user:
                if username not in desired_by_user:
                    changes.append(
                        {
                            "action": "destroy",
                            "resource_type": "security",
                            "name": username,
                            "parent": name,
                            "attrs": {"username": username},
                        }
                    )

        redirects_cfg = app_def.get("redirects")
        if redirects_cfg is not None or "redirects" in state["apps"].get(name, {}):
            remote_redirects = remote.get("redirects") or []
            desired_redirects = redirects_cfg or []
            existing_by_regex = {r["regex"]: r for r in remote_redirects}
            desired_by_regex = {r["regex"]: r for r in desired_redirects}

            for regex, redir in desired_by_regex.items():
                payload = build_redirect_payload(app_info["applicationId"], redir)
                if regex in existing_by_regex:
                    ex = existing_by_regex[regex]
                    diffs: dict = {}
                    for key in ("replacement", "permanent"):
                        old_val = ex.get(key)
                        new_val = payload.get(key)
                        if old_val != new_val:
                            diffs[key] = (old_val, new_val)
                    if diffs:
                        changes.append(
                            {
                                "action": "update",
                                "resource_type": "redirect",
                                "name": regex,
                                "parent": name,
                                "attrs": diffs,
                            }
                        )
                else:
                    changes.append(
                        {
                            "action": "create",
                            "resource_type": "redirect",
                            "name": regex,
                            "parent": name,
                            "attrs": {
                                "regex": regex,
                                "replacement": redir["replacement"],
                                "permanent": redir["permanent"],
                            },
                        }
                    )

            for regex, ex in existing_by_regex.items():
                if regex not in desired_by_regex:
                    changes.append(
                        {
                            "action": "destroy",
                            "resource_type": "redirect",
                            "name": regex,
                            "parent": name,
                            "attrs": {"regex": regex},
                        }
                    )

        schedules = app_def.get("schedules")
        if schedules is None and "schedules" not in state["apps"].get(name, {}):
            continue

        app_id = app_info["applicationId"]
        existing = client.get(
            "schedule.list",
            {"id": app_id, "scheduleType": "application"},
        )
        if not isinstance(existing, list):
            existing = []
        desired = schedules or []

        existing_by_name = {s["name"]: s for s in existing}
        desired_by_name = {s["name"]: s for s in desired}

        for sname, sched in desired_by_name.items():
            payload = build_schedule_payload(app_id, sched)
            if sname in existing_by_name:
                ex = existing_by_name[sname]
                diffs: dict = {}
                for key in (
                    "cronExpression",
                    "command",
                    "shellType",
                    "enabled",
                    "timezone",
                ):
                    old_val = ex.get(key)
                    new_val = payload.get(key)
                    if old_val != new_val:
                        diffs[key] = (old_val, new_val)
                if diffs:
                    changes.append(
                        {
                            "action": "update",
                            "resource_type": "schedule",
                            "name": sname,
                            "parent": name,
                            "attrs": diffs,
                        }
                    )
            else:
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "schedule",
                        "name": sname,
                        "parent": name,
                        "attrs": {
                            "cronExpression": sched["cronExpression"],
                            "command": sched["command"],
                        },
                    }
                )

        for sname, ex in existing_by_name.items():
            if sname not in desired_by_name:
                changes.append(
                    {
                        "action": "destroy",
                        "resource_type": "schedule",
                        "name": sname,
                        "parent": name,
                        "attrs": {"command": ex.get("command", "")},
                    }
                )

    # Volume backup diff (apps)
    for name, app_info in state["apps"].items():
        app_def = apps_by_name.get(name)
        if app_def is None:
            continue
        compose = app_info.get("source") == "compose"

        vb_cfg = app_def.get("volumeBackups")
        if vb_cfg is None and "volumeBackups" not in app_info:
            continue

        service_id_key = "composeId" if compose else "applicationId"
        service_type = "compose" if compose else "application"
        service_id = app_info[service_id_key]
        existing_vbs = client.get(
            "volumeBackups.list",
            {"id": service_id, "volumeBackupType": service_type},
        )
        if not isinstance(existing_vbs, list):
            existing_vbs = []
        existing_by_vb_name = {vb["name"]: vb for vb in existing_vbs}
        desired_by_vb_name = {vb["name"]: vb for vb in (vb_cfg or [])}

        for vb_name, vb_def in desired_by_vb_name.items():
            if vb_name not in existing_by_vb_name:
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "volume_backup",
                        "name": vb_name,
                        "parent": name,
                        "attrs": {
                            "volumeName": vb_def["volumeName"],
                            "cronExpression": vb_def["cronExpression"],
                            "destination": vb_def["destination"],
                            "keepLatestCount": vb_def.get("keepLatestCount"),
                        },
                    }
                )

        for vb_name in existing_by_vb_name:
            if vb_name not in desired_by_vb_name:
                changes.append(
                    {
                        "action": "destroy",
                        "resource_type": "volume_backup",
                        "name": vb_name,
                        "parent": name,
                        "attrs": {"name": vb_name},
                    }
                )

    # Destinations diff
    existing_dests = state.get("destinations", {})
    desired_dests = {d["name"]: d for d in cfg.get("destinations", [])}

    for name, dest_def in desired_dests.items():
        if name not in existing_dests:
            changes.append(
                {
                    "action": "create",
                    "resource_type": "destination",
                    "name": name,
                    "parent": None,
                    "attrs": {
                        "bucket": dest_def["bucket"],
                        "region": dest_def["region"],
                        "endpoint": dest_def["endpoint"],
                    },
                }
            )

    # Database backup diff
    for db_def in cfg.get("database", []):
        db_name = db_def["name"]
        db_info = state.get("database", {}).get(db_name)
        if not db_info:
            continue
        backups_cfg = db_def.get("backups")
        if backups_cfg is None and "backups" not in db_info:
            continue

        existing_backups = db_info.get("backups", {})
        desired_backups = {b["prefix"]: b for b in (backups_cfg or [])}

        for prefix, backup_def in desired_backups.items():
            if prefix not in existing_backups:
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "backup",
                        "name": prefix,
                        "parent": db_name,
                        "attrs": {
                            "schedule": backup_def["schedule"],
                            "destination": backup_def["destination"],
                            "keepLatestCount": backup_def.get("keepLatestCount"),
                        },
                    }
                )

        for prefix in existing_backups:
            if prefix not in desired_backups:
                changes.append(
                    {
                        "action": "destroy",
                        "resource_type": "backup",
                        "name": prefix,
                        "parent": db_name,
                        "attrs": {"prefix": prefix},
                    }
                )

    # Database volume backup diff
    for db_def in cfg.get("database", []):
        db_name = db_def["name"]
        db_info = state.get("database", {}).get(db_name)
        if not db_info:
            continue
        vb_cfg = db_def.get("volumeBackups")
        if vb_cfg is None and "volumeBackups" not in db_info:
            continue

        existing_vbs = db_info.get("volumeBackups", {})
        desired_vbs = {vb["name"]: vb for vb in (vb_cfg or [])}

        for vb_name, vb_def in desired_vbs.items():
            if vb_name not in existing_vbs:
                changes.append(
                    {
                        "action": "create",
                        "resource_type": "volume_backup",
                        "name": vb_name,
                        "parent": db_name,
                        "attrs": {
                            "volumeName": vb_def["volumeName"],
                            "cronExpression": vb_def["cronExpression"],
                            "destination": vb_def["destination"],
                            "keepLatestCount": vb_def.get("keepLatestCount"),
                        },
                    }
                )

        for vb_name in existing_vbs:
            if vb_name not in desired_vbs:
                changes.append(
                    {
                        "action": "destroy",
                        "resource_type": "volume_backup",
                        "name": vb_name,
                        "parent": db_name,
                        "attrs": {"name": vb_name},
                    }
                )


def compute_plan(
    client: DokployClient,
    cfg: dict,
    state_file: Path,
    repo_root: Path,
) -> list[dict]:
    """Compute the list of changes that apply would make, without executing them."""
    changes: list[dict] = []

    if not state_file.exists():
        _plan_initial_setup(cfg, repo_root, changes)
        return changes

    state = load_state(state_file)
    if not validate_state(client, state):
        _plan_initial_setup(cfg, repo_root, changes)
        return changes

    _plan_redeploy(client, cfg, state, repo_root, changes)
    return changes


def print_plan(changes: list[dict]) -> None:
    """Print a terraform-style plan output."""
    if not changes:
        print("\nNo changes. Infrastructure is up to date.")
        return

    symbols = {"create": "+", "update": "~", "destroy": "-"}

    print("\nic will perform the following actions:\n")
    print("Resource actions are indicated with the following symbols:")
    print("  + create")
    print("  ~ update")
    print("  - destroy\n")

    for change in changes:
        action = change["action"]
        sym = symbols[action]
        rtype = change["resource_type"]
        name = change["name"]
        parent = change.get("parent")
        attrs = change.get("attrs", {})

        header = f'{rtype} "{name}" ({parent})' if parent else f'{rtype} "{name}"'

        print(f"  {sym} {header} {{")

        if action == "create":
            for k, v in attrs.items():
                print(f"      + {k:20s} = {v}")
        elif action == "update":
            if rtype == "environment":
                for k in attrs.get("added", []):
                    print(f"      + {k}")
                for k in attrs.get("removed", []):
                    print(f"      - {k}")
            else:
                for k, (old, new) in attrs.items():
                    print(f"      ~ {k:20s} = {old!r} -> {new!r}")
        elif action == "destroy":
            for k, v in attrs.items():
                print(f"      - {k:20s} = {v}")

        print("    }")
        print()

    creates = sum(1 for c in changes if c["action"] == "create")
    updates = sum(1 for c in changes if c["action"] == "update")
    destroys = sum(1 for c in changes if c["action"] == "destroy")
    print(f"Plan: {creates} to create, {updates} to update, {destroys} to destroy.")


def cmd_plan(
    client: DokployClient,
    cfg: dict,
    state_file: Path,
    repo_root: Path,
) -> None:
    """Show what changes apply would make without executing them."""
    changes = compute_plan(client, cfg, state_file, repo_root)
    print_plan(changes)
