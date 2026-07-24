---
id: TASK-029
title: Close API coverage gaps vs official Dokploy CLI
status: Done
assignee: []
created_date: '2026-07-24 15:52'
updated_date: '2026-07-24 17:51'
labels:
  - enhancement
  - api-coverage
dependencies: []
references:
  - /Users/lance/.claude/plans/an-official-dokploy-cli-sunny-steele.md
  - 'https://github.com/Dokploy/cli'
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The official @dokploy/cli provides 449 commands with 100% OpenAPI coverage; icarus is a declarative deploy tool (dokploy.yml + reconcile) and intentionally covers a focused subset. This initiative closes the gaps that fit icarus's declarative model and adds genuinely useful operational commands, while explicitly recording panel-admin and imperative-lifecycle groups as non-goals.

Scope confirmed with user:
- IN: declarative-completeness gaps within groups icarus already handles; the git-provider group; manual + volume backups.
- OUT (this round): imperative lifecycle verbs (start/stop/reload/rebuild/rollback/cancelDeployment) — icarus stays declarative.
- Ignored per request: non-github forges (bitbucket/gitea/gitlab) and license-key.

Full analysis and file map: plan at ~/.claude/plans/an-official-dokploy-cli-sunny-steele.md. Target API version: Dokploy 0.29.13 (schemas/src/openapi_0.29.13.json).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Declarative-completeness subtask delivered (DB update/rebuild reconcile, registry/destination connection tests, certificate removal, schedule.runManually)
- [x] #2 Manual + volume backups subtask delivered
- [x] #3 git-provider list/remove subtask delivered
- [x] #4 Non-goals documented in docs/api.md with rationale table
- [x] #5 All new behavior has tests in existing tests/ files and validated live against Dokploy 0.29.13
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All four subtasks delivered and merged to main:
- 029.01 Declarative-completeness for existing resource groups (Done)
- 029.02 Manual + volume backups: ic backup <db> [--prefix] [--list], volumeBackups: config block, ic backup <resource> --volume <name> (Done)
- 029.03 git-provider list/remove commands (Done)
- 029.04 Non-goals documented in docs/api.md with rationale table (Done)

486 tests pass, ruff format clean, markdownlint clean on docs/api.md. AC #5 (live validation against Dokploy 0.29.13) covered by prior subtask work; not independently re-validated live in this merge pass.
<!-- SECTION:FINAL_SUMMARY:END -->
