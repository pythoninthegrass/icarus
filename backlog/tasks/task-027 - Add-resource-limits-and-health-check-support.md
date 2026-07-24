---
id: TASK-027
title: Add resource limits and health check support
status: Done
assignee: []
created_date: '2026-03-23 18:05'
updated_date: '2026-07-24 19:40'
labels:
  - gap-analysis
  - new-resource
milestone: m-0
dependencies: []
references:
  - main.py
  - schemas/dokploy.schema.json
modified_files:
  - src/icarus/payloads.py
  - src/icarus/reconcile.py
  - src/icarus/plan.py
  - src/icarus/__init__.py
  - tests/test_unit.py
  - schemas/dokploy.schema.json
  - docs/configuration.md
  - docs/api.md
  - dokploy.yml.example
priority: low
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The TF provider exposes resource limits (CPU/memory) and health check configuration. Icarus has neither. Add optional `resources` (cpu, memory limits/reservations) and `healthCheck` (command, interval, timeout, retries) config to app definitions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Apps can declare resource limits in dokploy.yml (cpu, memory)
- [x] #2 Apps can declare health checks in dokploy.yml (command, interval, timeout, retries)
- [x] #3 Configs are applied via application.update API
- [x] #4 Apps can declare restartPolicy in dokploy.yml (condition, delay, maxAttempts, window)
- [x] #5 Human-friendly units are accepted (memory: 512M/1G, cpu: 0.5, durations: 30s/1m) and converted to API values
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Added parse_memory_bytes (1024-based K/M/G/T suffixes -> byte string), parse_cpu_nanos (CPUs -> nanocore string), and parse_duration_nanos (ms/s/m/h -> nanoseconds) to payloads.py, plus build_health_check_swarm/build_restart_policy_swarm translators. build_app_settings_payload now maps resources -> memoryLimit/memoryReservation/cpuLimit/cpuReservation, healthCheck -> healthCheckSwarm, restartPolicy -> restartPolicySwarm; applied on setup (commands.py step 7) and diffed against application.one on redeploy (reconcile_app_settings now diffs the whole settings payload instead of a hardcoded key list). ic plan previews the converted values in both initial-setup and redeploy paths via the same payload builder. Schema ($defs resources/health_check/restart_policy, referenced from apps and env overrides), docs/configuration.md, docs/api.md quirk entry, and dokploy.yml.example updated. Field shapes verified against openapi_0.29.13 and the ahmedali6/terraform-provider-dokploy application resource. 544 tests pass.
<!-- SECTION:NOTES:END -->
