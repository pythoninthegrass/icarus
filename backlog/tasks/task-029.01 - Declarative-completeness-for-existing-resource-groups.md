---
id: TASK-029.01
title: Declarative completeness for existing resource groups
status: Done
assignee: []
created_date: '2026-07-24 15:53'
updated_date: '2026-07-24 16:06'
labels: []
dependencies: []
references:
  - /Users/lance/.claude/plans/an-official-dokploy-cli-sunny-steele.md
modified_files:
  - src/icarus/reconcile.py
  - src/icarus/payloads.py
  - src/icarus/commands.py
  - src/icarus/ssh.py
  - src/icarus/cli.py
  - src/icarus/__init__.py
  - tests/test_unit.py
  - docs/api.md
  - docs/configuration.md
parent_task_id: TASK-029
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Close coverage holes within groups icarus already handles. All additions run inside the existing setup/apply/plan reconcile flow — no new imperative lifecycle verbs.

Work (files: src/icarus/reconcile.py, src/icarus/payloads.py, src/icarus/plan.py, plus schema/docs):
- Database update + rebuild: add reconcile_databases that diffs config vs <type>.one and calls <type>.update on drift (image/description/env), modeled on reconcile_app_settings (reconcile.py:395). Wire <type>.rebuild into trigger when a DB image changes. Reuse database_endpoint (payloads.py:21).
- Registry/destination connection tests: call registry.testRegistryById and destination.testConnection after create/update in reconcile_registries (reconcile.py:432) and reconcile_destinations (reconcile.py:555); surface failures in check.
- Certificate removal: reconcile_certificates (reconcile.py:587) only creates today — add delete-of-removed via certificates.remove, matching the create/update/delete pattern in reconcile_domains/reconcile_mounts.
- schedule.runManually: expose ic run-schedule <app> <schedule-name> (resolve scheduleId from state, post schedule.runManually). Register in cli.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 reconcile_databases updates DB drift (image/description/env) and rebuilds on image change
- [x] #2 Registry and destination connection tests run after create/update and failures surface in check
- [x] #3 Certificate reconcile deletes removed certificates via certificates.remove
- [x] #4 ic run-schedule <app> <name> triggers schedule.runManually
- [x] #5 Unit + integration tests added to existing tests/ files; docs/configuration.md and docs/api.md updated
<!-- AC:END -->



## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented declarative completeness for existing resource groups:

1. `reconcile_databases` (reconcile.py) diffs `dockerImage`/`description` against `<type>.one` and calls `<type>.update`; a `dockerImage` change also triggers `<type>.rebuild`. Wired into `cmd_apply`'s redeploy block. New `build_database_update_payload` in payloads.py.
2. `reconcile_registries`/`reconcile_destinations` now call `registry.testRegistry`/`destination.testConnection` after create/update (reusing existing create-payload builders), raising `SystemExit` on `httpx.HTTPStatusError` to fail fast on bad credentials.
3. `reconcile_certificates` now deletes certificates removed from config via `certificates.remove`, scoped against icarus's own tracked state (`state["certificates"]`) rather than the org-wide `certificates.all`, so certs not created by icarus are never touched.
4. `ic run-schedule <app> <name>` triggers `schedule.runManually`. Added `resolve_app_name` (extracted from `resolve_app_for_exec`) in ssh.py, `cmd_run_schedule` in commands.py, and CLI wiring in cli.py.

All new functionality built TDD (tests written first, confirmed red, then implemented green). Full suite: 456 passed. `ruff check`/`ruff format --check` clean. Docs updated: docs/api.md (new endpoint notes) and docs/configuration.md (run-schedule usage, certificate deletion scoping, database update/rebuild behavior). No schema changes needed — only existing `dockerImage`/`description` fields are diffed.
<!-- SECTION:FINAL_SUMMARY:END -->
