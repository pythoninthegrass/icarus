---
id: TASK-004
title: Import existing Dokploy projects and services into local state
status: In Progress
assignee: []
created_date: '2026-03-07 03:06'
updated_date: '2026-03-07 03:07'
labels:
  - implementation
  - cli
dependencies: []
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add an `import` subcommand to `dokploy.py` that connects to a running Dokploy instance, finds a project by name (from `dokploy.yml`), and writes the matching state file (`.dokploy-state/<env>.json`) so that subsequent commands (`env`, `deploy`, `status`, `destroy`) work against the existing project without re-running `setup`.

This avoids the current problem where a project already exists on the server but there's no local state, forcing the user to run `setup` and create a duplicate.

**Likely approach:**
- `dokploy.py --env prod import` queries `project.all`, matches by `project.name`
- Walks the project's environments and applications to build the state dict (`projectId`, `environmentId`, `apps.{name}.applicationId`, `apps.{name}.appName`)
- Saves to `.dokploy-state/<env>.json`
- Fails clearly if the project doesn't exist on the server or if a state file already exists
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Running `dokploy.py --env prod import` against a server with an existing project writes a correct state file
- [ ] #2 Subsequent `status`, `env`, `deploy`, and `destroy` commands work using the imported state
- [ ] #3 Fails with a clear error if no matching project exists on the server
- [ ] #4 Fails with a clear error if a state file already exists (no silent overwrite)
- [ ] #5 All app names in dokploy.yml are matched to their server-side applicationId and appName
<!-- AC:END -->
