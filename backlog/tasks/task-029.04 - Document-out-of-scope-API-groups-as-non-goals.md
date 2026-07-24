---
id: TASK-029.04
title: Document out-of-scope API groups as non-goals
status: Done
assignee: []
created_date: '2026-07-24 15:53'
updated_date: '2026-07-24 17:22'
labels: []
dependencies: []
references:
  - /Users/lance/.claude/plans/an-official-dokploy-cli-sunny-steele.md
modified_files:
  - docs/api.md
parent_task_id: TASK-029
priority: medium
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Record the deliberate boundary so the gap vs the official CLI is intentional, not accidental. Add a "Non-goals" section to docs/api.md with a rationale table covering panel-admin and imperative groups icarus does not wrap:

- notification (38) — panel admin; future candidate: declarative notify-on-deploy
- settings (49) — server/Traefik admin; clean already covers the prune subset via SSH
- user (18), organization (10), sso (10), stripe (7), admin (1) — account/billing/tenant admin
- ai (9) — Dokploy AI features
- cluster (4), swarm (3) — node/infra management
- patch (12) — server-side file patching
- docker (7) — redundant; icarus reads containers over SSH (ssh.py)
- preview-deployment (4), rollback (2) — imperative deploy verbs, deferred
- lifecycle verbs (start/stop/reload/rebuild/cancel/kill) — imperative; icarus stays declarative this round
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 docs/api.md has a Non-goals section with a rationale table for each out-of-scope group
- [x] #2 Future candidates (notifications, lifecycle verbs) noted as such
- [x] #3 markdownlint passes on docs/api.md
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a "Non-goals" section to docs/api.md with a rationale table covering all out-of-scope API groups (notification, settings, user/organization/sso/stripe/admin, ai, cluster/swarm, patch, docker, preview-deployment/rollback, and lifecycle verbs). notification and lifecycle verbs are called out as future candidates; the rest are documented as deliberately out of scope. markdownlint passes clean.
<!-- SECTION:FINAL_SUMMARY:END -->
