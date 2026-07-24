---
id: TASK-029.03
title: Add git-provider list/remove commands
status: To Do
assignee: []
created_date: '2026-07-24 15:53'
labels: []
dependencies: []
references:
  - /Users/lance/.claude/plans/an-official-dokploy-cli-sunny-steele.md
parent_task_id: TASK-029
ordinal: 16000
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
- [ ] #1 ic git-provider list prints all configured git providers via gitProvider.getAll
- [ ] #2 ic git-provider remove <id> disconnects a provider via gitProvider.remove
- [ ] #3 Command registered in cli.py argparse + dispatch
- [ ] #4 Tests added to existing tests/ files; docs/api.md updated
<!-- AC:END -->
