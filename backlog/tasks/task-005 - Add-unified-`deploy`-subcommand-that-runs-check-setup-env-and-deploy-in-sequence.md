---
id: TASK-005
title: >-
  Add unified `deploy` subcommand that runs check, setup, env, and deploy in
  sequence
status: Done
assignee: []
created_date: '2026-03-07 03:06'
updated_date: '2026-03-07 03:38'
labels:
  - implementation
  - cli
  - dx
dependencies: []
priority: medium
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a high-level workflow so that `dokploy.py --env prod deploy` (or a new name like `up` if `deploy` conflicts) performs the full lifecycle automatically:

1. `check` — pre-flight validation
2. `setup` — create project/apps (skip if state file already exists)
3. `env` — push environment variables
4. `deploy` — trigger deployments

This removes the need to manually run four separate commands for a fresh deployment. Each step should print its phase header so the user can follow progress. If any step fails, stop immediately.

**Open question:** The existing `deploy` subcommand already exists. Options:
- Rename the current `deploy` to something more specific (e.g., `trigger`) and reuse `deploy` for the unified workflow
- Use a new name like `up` or `full-deploy` for the unified workflow
- Keep `deploy` as-is and add `up` as the unified command
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Running the unified command on a fresh project (no state file) performs check, setup, env, and deploy in order
- [x] #2 Running the unified command on an existing project (state file present) skips setup and runs check, env, deploy
- [x] #3 Each phase prints a clear header so the user can follow progress
- [x] #4 Failure in any phase stops execution immediately with a non-zero exit code
- [x] #5 The existing granular subcommands still work independently
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Renamed `cmd_deploy` to `cmd_trigger` and added a new `cmd_deploy` orchestrator that runs check -> setup (skipped if state file exists) -> env -> trigger with phase headers. Updated `main()` to include `trigger` as a CLI choice and route `deploy` to the orchestrator. Updated integration tests to use `cmd_trigger` for the old behavior. Added `TestUnifiedDeploy` with 7 tests covering all ACs.
<!-- SECTION:FINAL_SUMMARY:END -->
