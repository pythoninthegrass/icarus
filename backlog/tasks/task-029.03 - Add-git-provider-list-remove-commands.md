---
id: TASK-029.03
title: Add git-provider list/remove commands
status: Done
assignee: []
created_date: '2026-07-24 15:53'
updated_date: '2026-07-24 17:30'
labels: []
dependencies: []
references:
  - /Users/lance/.claude/plans/an-official-dokploy-cli-sunny-steele.md
modified_files:
  - src/icarus/commands.py
  - src/icarus/cli.py
  - src/icarus/__init__.py
  - tests/test_unit.py
  - docs/api.md
parent_task_id: TASK-029
priority: medium
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The shared gitProvider API group has getAll and remove. Add generic provider inspection and cleanup, complementing the existing github-specific provider resolution (payloads.py:72, resolve_github_provider) which only reads github providers for repo matching.

Work (files: src/icarus/commands.py, src/icarus/cli.py):
- ic git-provider list -> gitProvider.getAll (lists all configured providers across types).
- ic git-provider remove <id> -> gitProvider.remove ({gitProviderId}).
Keep github save/wiring as-is.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ic git-provider list prints all configured git providers via gitProvider.getAll
- [x] #2 ic git-provider remove <id> disconnects a provider via gitProvider.remove
- [x] #3 Command registered in cli.py argparse + dispatch
- [x] #4 Tests added to existing tests/ files; docs/api.md updated
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added `ic git-provider list` (gitProvider.getAll) and `ic git-provider remove <id>` (gitProvider.remove) as account-level commands, dispatched in cli.py before repo/config loading (client constructed but no repo_root/state_file/cfg). Left resolve_github_provider and github-specific wiring untouched. Added cmd_git_provider_list/cmd_git_provider_remove tests plus argparse subparser tests to tests/test_unit.py. Documented both endpoints and commands in docs/api.md. All 467 tests pass; ruff format clean.
<!-- SECTION:FINAL_SUMMARY:END -->
