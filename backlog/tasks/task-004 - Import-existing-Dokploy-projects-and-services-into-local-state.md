---
id: TASK-004
title: Import existing Dokploy projects and services into local state
status: Done
assignee: []
created_date: '2026-03-07 03:06'
updated_date: '2026-03-07 03:33'
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
- [x] #1 Running `dokploy.py --env prod import` against a server with an existing project writes a correct state file
- [x] #2 Subsequent `status`, `env`, `deploy`, and `destroy` commands work using the imported state
- [x] #3 Fails with a clear error if no matching project exists on the server
- [x] #4 Fails with a clear error if a state file already exists (no silent overwrite)
- [x] #5 All app names in dokploy.yml are matched to their server-side applicationId and appName
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation

### `cmd_import` function (`dokploy.py:602-660`)
- Checks state file doesn't already exist (exits with error if it does)
- Calls `GET /api/project.all` to fetch all projects
- Matches by `cfg["project"]["name"]`
- Uses first environment from the matched project
- Matches each app from `cfg["apps"]` to server-side applications by `name`
- Builds state dict with `projectId`, `environmentId`, and app mappings
- Saves state via `save_state()`

### CLI integration (`main()`)
- Added "import" to argparse choices
- Added `case "import"` to match statement routing to `cmd_import(client, cfg, state_file)`

### Tests (`tests/test_integration.py`)
- `TestCmdImport` class with 6 test cases:
  - `test_happy_path` — single app import writes correct state
  - `test_multi_app` — 5-app config maps all apps correctly
  - `test_no_matching_project` — exits when project name not found
  - `test_state_file_already_exists` — exits when state file present
  - `test_missing_app_on_server` — exits when config apps not on server
  - `test_empty_project_list` — exits when no projects exist
- Helper `_make_project_all_response()` builds mock `project.all` responses

### Branch
`004-import` worktree at `dokploy_seed.004-import/`
<!-- SECTION:NOTES:END -->
