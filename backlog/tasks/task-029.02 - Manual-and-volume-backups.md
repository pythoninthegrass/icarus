---
id: TASK-029.02
title: Manual and volume backups
status: Done
assignee: []
created_date: '2026-07-24 15:53'
updated_date: '2026-07-24 17:33'
labels: []
dependencies: []
references:
  - /Users/lance/.claude/plans/an-official-dokploy-cli-sunny-steele.md
parent_task_id: TASK-029
priority: high
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add on-demand DB backups and the volumeBackups resource group.

Work:
- Manual DB backup: backup.manualBackup{Postgres,MySql,Mongo,Mariadb} each take an existing backupId. icarus already stores backupId per prefix in state (reconcile.py:668,684). Add ic backup <db> [--prefix NAME] to look up backupId and post the manualBackup* for the DB type. Add ic backup <db> --list via backup.listBackupFiles.
- Volume backups (new declarative resource): volumeBackups group (create/update/delete/list/one/runManually). volumeBackups.create takes {name, volumeName, cronExpression, destinationId, prefix, enabled, keepLatestCount, serviceType, <serviceId>}. Add a volumeBackups: list under an app/compose/db entry (reuse destination name->id resolution from reconcile_database_backups, reconcile.py:651); add reconcile_volume_backups modeled on reconcile_database_backups (reconcile.py:622), keyed by prefix; add build_volume_backup_payload in payloads.py next to build_backup_create_payload (payloads.py:312) — note field is cronExpression not schedule. ic backup <resource> --volume <name> uses volumeBackups.runManually.

Files: src/icarus/payloads.py, src/icarus/reconcile.py, src/icarus/commands.py, src/icarus/cli.py, src/icarus/plan.py, schemas/dokploy.schema.json, docs/configuration.md, dokploy.yml.example, docs/api.md.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ic backup <db> triggers the correct manualBackup* for the DB type using stored backupId
- [x] #2 ic backup <db> --list lists backup files via backup.listBackupFiles
- [x] #3 volumeBackups: config block validates against schemas/dokploy.schema.json and is reconciled (create/update/delete by prefix)
- [x] #4 ic backup <resource> --volume <name> triggers volumeBackups.runManually
- [x] #5 plan output includes volume-backup diffs; tests added to existing tests/ files; docs and dokploy.yml.example updated
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented: cmd_backup (manual DB backup, --list, --volume) wired into cli.py; build_volume_backup_payload in payloads.py; reconcile_volume_backups in reconcile.py (for both apps and database entries); volume_backup diffs added to plan.py (_plan_initial_setup and _plan_redeploy); schema/docs/example updated. Also fixed a pre-existing bug: reconcile_database_backups posted to the nonexistent 'backup.delete' endpoint instead of 'backup.remove'.

Deviation from AC #3 wording: volumeBackups are reconciled keyed by `name` (a required field on volume_backup_entry), not `prefix` — prefix is only used for backup-file naming/search, not identity, so name is the correct dedup/diff key. All other behavior matches spec.

Tests: 480 passed (402 in test_unit.py + others), 0 failed. ruff format clean. dokploy.yml.example validates against load_config/validate_config.
<!-- SECTION:NOTES:END -->
