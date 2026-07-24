---
id: TASK-028
title: Cleanup orphaned headless Docker services
status: Done
assignee: []
created_date: '2026-04-01 18:19'
updated_date: '2026-07-24 18:34'
labels:
  - bug
  - cleanup
milestone: m-0
dependencies: []
references:
  - src/icarus/ssh.py
  - src/icarus/commands.py
modified_files:
  - src/icarus/ssh.py
  - src/icarus/commands.py
  - src/icarus/cli.py
  - src/icarus/__init__.py
  - tests/test_unit.py
  - tests/test_integration.py
  - docs/api.md
  - README.md
  - AGENTS.md
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`cleanup_stale_routes` only detects orphaned services that have Traefik route configs matching the project's domains. Headless services (celery workers, beat schedulers, background jobs) never have Traefik configs, so they are invisible to the current cleanup logic and persist indefinitely after redeployment.

This caused a production incident where an old celery worker (`app-connect-back-end-hard-drive-d5376f`) remained running on the same Redis broker as the current worker (`app-calculate-wireless-system-q118zj`). The stale worker competed for tasks and executed them with old, buggy code, causing `FileNotFoundError` failures that were difficult to diagnose because the current container had the correct code.

### Root cause

Dokploy generates random `app-*` service names. When `ic` creates a new deployment (new state file or `ic setup` after destroy), old Docker Swarm services from a previous deployment are not removed. The state file only tracks the *current* mapping, not previous ones. `cleanup_stale_routes` (the only cleanup mechanism) relies entirely on Traefik config file matching, which only covers services with domain routing.

### Current behavior

1. `cleanup_stale_routes` lists `/etc/dokploy/traefik/dynamic/*.yml`
2. Matches filenames (appNames) against current state's appNames
3. For non-matching names, checks if the Traefik config routes to one of the project's domains
4. Only removes services that match both criteria (not in state AND routing to our domain)
5. Headless services (no domain, no Traefik config) are completely ignored

### Desired behavior

Cleanup should also detect and remove orphaned Docker Swarm services that belong to the same Dokploy project but are not in the current state file. Two approaches (not mutually exclusive):

**Approach A: Dokploy API-based cleanup**
Query `project.all` or the project's environment to get all applications associated with the projectId, compare against the state file's appNames, and remove any that aren't tracked. This is the most reliable approach since it uses Dokploy's own records.

**Approach B: Docker Swarm service enumeration via SSH**
List all Docker services (`docker service ls`), identify `app-*` services that share the same Docker network as the project's known services but aren't in the current state, and scale them to 0 or remove them. This catches services even if Dokploy's API doesn't know about them.

### Affected commands

- `cmd_apply` (line 694-695 in commands.py) -- should run expanded cleanup on redeploy
- `cmd_clean` (line 795-800) -- should include headless orphan cleanup
- `cmd_destroy` (line 803-815) -- should clean headless orphans before project deletion
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `cleanup_stale_routes` (or a new companion function) detects orphaned headless services not in the current state file
- [x] #2 Orphaned services are scaled to 0 or removed during `ic apply`, `ic clean`, and `ic destroy`
- [x] #3 Cleanup only targets services belonging to the same Dokploy project (not other projects sharing the server)
- [x] #4 Cleanup prints what it finds and removes (existing log style: "Removed: app-foo-bar-xyz")
- [x] #5 Dry-run or confirmation prompt before removing services (safety net)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented both approaches, wired into apply (redeploy path), clean, and destroy via a single cleanup_orphans(client, cfg, state, dry_run=False) orchestrator in commands.py.

Approach A (API): find_untracked_project_services compares project.one's environment (matching state environmentId only) against state-tracked applicationId/composeId; cleanup_orphaned_project_apps removes untracked apps via application.delete / compose.delete (deleteVolumes=false) after a [y/N] confirmation. Databases are deliberately excluded (data-loss risk). A 404 on project.one (project already deleted server-side) is skipped gracefully.

Approach B (SSH): DEVIATION from the task's proposed network-overlap heuristic. Live inspection showed Dokploy swarm services carry no labels and ALL share dokploy-network, so network membership cannot scope to a project. Instead a global appName registry is built (project.all + project.one per project, because project.all omits appName on nested services - documented in docs/api.md). find_orphaned_services flags a service only if its owner name (compose stacks: prefix before first "_") is unknown to EVERY Dokploy project AND matches an expected prefix ("app-" legacy names or prefixes derived from state-tracked appNames). Dokploy system services always skipped. If the registry fetch fails, the swarm scan aborts rather than deleting from a partial known-set. This satisfies AC#3 more conservatively: other projects' services are never touched.

Safety: ic clean --dry-run lists without removing; non-dry-run prompts confirm() [y/N] per stage. Swarm stage requires DOKPLOY_SSH_HOST (skipped with message otherwise).

Verified live against hosty (85.31.233.80): dry run from hh_quote_tool found exactly the 18 legacy app-* services unknown to all 16 projects; cross-checked docker service ls vs full API appName registry - the diff matched the 18 flagged services exactly, and all 12 legacy app-* names still known to the API were protected. TDD throughout; full suite 519 passed.

Note: repo .env in hh_quote_tool has stale DOKPLOY_SSH_HOST=10.5.162.12 (unreachable); override with DOKPLOY_SSH_HOST=85.31.233.80 DOKPLOY_SSH_USER=root.
<!-- SECTION:NOTES:END -->
