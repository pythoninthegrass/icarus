---
id: TASK-029
title: Close API coverage gaps vs official Dokploy CLI
status: To Do
assignee: []
created_date: '2026-07-24 15:52'
labels:
  - enhancement
  - api-coverage
dependencies: []
references:
  - /Users/lance/.claude/plans/an-official-dokploy-cli-sunny-steele.md
  - 'https://github.com/Dokploy/cli'
ordinal: 13000
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
- [ ] #1 Declarative-completeness subtask delivered (DB update/rebuild reconcile, registry/destination connection tests, certificate removal, schedule.runManually)
- [ ] #2 Manual + volume backups subtask delivered
- [ ] #3 git-provider list/remove subtask delivered
- [ ] #4 Non-goals documented in docs/api.md with rationale table
- [ ] #5 All new behavior has tests in existing tests/ files and validated live against Dokploy 0.29.13
<!-- AC:END -->
