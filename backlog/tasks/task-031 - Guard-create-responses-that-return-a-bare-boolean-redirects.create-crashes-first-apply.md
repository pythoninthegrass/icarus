---
id: TASK-031
title: >-
  Guard create responses that return a bare boolean (redirects.create crashes
  first apply)
status: Done
assignee:
  - Assistant
created_date: '2026-11-08T22:42:00Z'
updated_date: '2026-11-08T23:01:00Z'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`reconcile_redirects()` crashes the first time a redirect is created for an app: Dokploy v0.31.0's `POST /api/redirects.create` returns a bare `true`, and `src/icarus/reconcile.py:248` indexes `resp["redirectId"]`.

Repro: give an app a `redirects:` entry in `dokploy.yml` while it has none on the server, then run `ic --env prod apply`:

```
File "src/icarus/reconcile.py", line 248, in reconcile_redirects
    result_state[regex] = {"redirectId": resp["redirectId"]}
TypeError: 'bool' object is not subscriptable
```

### Not transient — deterministic on first create, masked afterwards

The redirect *is* created on the server before the exception, so a second `apply` finds the regex in `existing_by_regex`, takes the update path and succeeds. That makes it look flaky. The damage is in the first run: `cmd_apply` aborts inside phase 2, so every later reconcile is skipped (schedules, mounts, ports, security, app settings, databases, database and volume backups) and phase 4 never triggers a deploy. The panel is left partially converged, the state file has no `redirects` key, and the retry that "worked" hides what was skipped.

### A class of bug, not one call site

Ten places in `src/icarus/reconcile.py` index `resp["<kind>Id"]` straight after `client.post("<group>.create")`: schedule (L69), mounts (L139), port (L181), redirects (L248), domain (L360), registry (L463), security (L530), destination (L591), certificates (L634), backup (L726). The setup path in `src/icarus/commands.py` repeats the pattern (registry L261, destination L283, certificates L304, compose L337, port L483, schedule L499, security L515, redirects L532).

Observed live against Dokploy v0.31.0: `redirects.create` returns `true`; `domain.create` returns an object carrying `domainId`. The vendored `schemas/src/openapi_0.31.0.json` types all ten create responses as untyped objects, so the spec cannot predict which endpoints return a boolean — each needs checking against a live panel, or defensive handling.

The integration suite cannot catch this: `tests/test_integration.py:282` mocks `redirects.create` with a comment that it "needs a redirectId in the response" and returns `{"redirectId": …}`, encoding an assumption the real API does not satisfy.

### Fix

One helper that takes the create response plus a lookup closure and returns the id, tolerating a non-object response by re-reading the parent resource (`application.one` → `redirects` matched by regex, `domain.byApplicationId` by host, and so on).

Worth deciding alongside it: a reconcile step that raises should not abort the whole pipeline. Collecting failures, running the remaining steps plus the trigger, and exiting non-zero at the end keeps a bookkeeping problem from silently half-applying a deployment.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Creating a redirect through ic apply succeeds against Dokploy v0.31.0 with no traceback, and the state file records the real redirectId
- [x] #2 Every create path tolerates a non-object response by re-reading the parent resource for the id, or fails with an error naming the endpoint: the ten reconcile.py sites and the commands.py setup equivalents
- [x] #3 Tests cover the boolean-response shape, not only the object shape the current integration mock returns, so the regression cannot return
- [x] #4 A failing reconcile step no longer silently skips the remaining phases and the deploy trigger: either all phases run and the command exits non-zero, or it aborts before any server mutation
- [x] #5 The pytest suite passes and the change lands as a conventional commit with no attribution
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Approach

Verified against upstream Dokploy source (v0.31.0 **and** v0.30.10, `apps/dokploy/server/api/routers/*`) - the OpenAPI spec is useless here because it types every create 200 as an untyped object:

| create procedure | returns | icarus impact today |
|---|---|---|
| `redirects.create` | `true` | TypeError (the reported crash) |
| `security.create` | `true` | TypeError (same bug, not yet hit) |
| `backup.create` | nothing (`undefined`) | KeyError `backupId` |
| mount / port / schedule / domain / registry / destination / certificate / volume-backups / compose / application / project / postgres / mysql / mariadb / mongo / redis | created row | fine |

(`libsql.create` also returns `true` upstream but icarus has no libsql support.)

### 1. Shared helper - `src/icarus/client.py`

- `CreateIdError(RuntimeError)` - message names the endpoint, the missing id key, and the read that failed to recover it.
- `create_and_get_id(client, endpoint, payload, id_key, *, read_endpoint, read_params=None, read_container=None, match=None)`: POST, take `resp[id_key]` when the response is a dict carrying a non-empty string; otherwise re-read `read_endpoint` (drilling into `read_container` for `application.one` style parents), match rows on every `match` key, return that row's id; else raise `CreateIdError`.
- `match` must be non-empty for the re-read path - never return the first row blindly.
- Widen `DokployClient.post` return annotation: it is `Any`, not `dict`.

### 2. Convert every create call site to the helper

- reconcile.py (11): schedule L69, mounts L139, port L181, redirects L248, domain L360, registry L463, security L530, destination L591, certificates L634, backup L726, volumeBackups L778.
- commands.py setup (12): registry L261, destination L283, certificates L304, compose L337, application L354, port L483, schedule L499, security L515, redirects L532, database L541, backup L568 (project.create L215 keeps its nested shape - it is not a `<kind>Id` index).
- Re-read sources: `application.one` (redirects/mounts/ports/security), `schedule.list`, `domain.byApplicationId`/`domain.byComposeId`, `registry.all`, `destination.all`, `certificates.all`, `backup.all`, `volumeBackups.list`, `project.one` (setup: app/compose ids).
- Where no safe lookup exists, pass no `match` and let it raise `CreateIdError` naming the endpoint (AC #2 allows either).

### 3. Don't let one reconcile step sink the pipeline - `cmd_apply`

Turn phase-3 reconcile sequence into a labeled step list; each step runs inside try/`except Exception` (SystemExit from the registry and destination connection tests still aborts - that is a deliberate loud failure, not a silent one). Collect labels, print each failure, run phase 4 anyway, then `sys.exit(1)` with the failed-step list.

### 4. Tests (existing files only)

- test_unit.py: helper matrix (dict / `True` / `None` / empty dict / missing key / lookup recovery / CreateIdError message), boolean-shape tests for the redirects + security + backup reconcilers, and a cmd_apply test proving later reconciles and the trigger still run and the command exits non-zero.
- test_integration.py: flip the `redirects.create` mock (L282-289) to the real `json=True` shape and mock the follow-up read; keep one object-shape mock so both are covered.

### 5. Docs

Record the per-endpoint response shapes in `docs/api.md` (AGENTS.md: API behavior changes go there).

### Risks

- Re-read must not resurrect a stale row when two resources share a match key - mitigated by matching the full natural key used by the reconciler (regex, mountPath, publishedPort, name, username, prefix).
- Broad `except Exception` in apply could mask real API faults - kept loud: printed per step and summarized at exit.

### Deviation recorded during implementation

`project.one` is not used as a re-read source. `compose.create`, `application.create` and the database creates need `appName` as well as the id and have no flat parent read, so they call `created_row` with no lookup and raise `CreateIdError` naming the endpoint if the row is missing (AC #2 allows either). `project.create` keeps its nested `{project, environment}` handling unchanged.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Verification evidence

- Live against https://dokploy.xyz (Dokploy v0.31.0, the version in the report): a throwaway project with one nginx app and a `redirects:` entry ran `ic --env verify apply` end to end with no traceback. State recorded `redirectId: tQAR6dO2GpHtH99mpAscw`, byte-identical to the row `application.one` returns. A second apply added a second redirect through the reconcile path (the reported crash site) and recorded `5VXr1KxBIz36OYu5igJaJ`, again matching the server. `destroy` removed the project; `project.all` shows no leftovers.
- Suite: `uv run pytest tests/ -q` -> 575 passed, 5 deselected. `uv run ruff check` and `ruff format --check` clean; `markdownlint` and `yamllint` clean on the touched files.

## Deviations from the plan

- `compose.create`, `application.create` and the database creates keep `client.post` + `created_row` with no parent lookup instead of recovering ids via `project.one`: they also need `appName` (which `project.one` returns only per project, costing an extra read per project) and have no flat parent read. They fail with `CreateIdError` naming the endpoint, which AC #2 allows explicitly.
- `project.create` is untouched: it returns the nested `{project, environment}` shape, not a `<kind>Id` index.
- Domain recovery compares `path` only when it is not the root `/`, because Dokploy stores an omitted path as `"/"`.

## Notes for review

- `SystemExit` is deliberately not caught in `_run_reconcile_phases`, so a failed registry/destination connection test still aborts `apply` instead of being collected as a step failure.
- The `redirects.create` mock in `_setup_router` now replays the real `json=True` response; the object shape stays covered by the mock in `TestCmdSetup.test_repo_name_is_not_owner_repo` and by the unit sweep.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## What changed

Dokploy's create endpoints do not agree on their responses, and icarus indexed every create reply as a mapping. `redirects.create` and `security.create` answer with a bare `true` and `backup.create` answers with nothing, so the first `ic apply` that added a redirect raised `TypeError: 'bool' object is not subscriptable` after the redirect already existed on the server, and `cmd_apply` died inside phase 3 - skipping every later reconcile step and the deploy trigger.

- `src/icarus/client.py`: added `CreateIdError`, `created_row`, and `create_and_get_id`; widened `DokployClient.post` to `Any`. A create whose response carries no id re-reads the parent resource and matches the row on its natural key; when no lookup is configured or nothing matches, the raised error names the endpoint.
- `src/icarus/reconcile.py`: all 11 create sites (schedule, mounts, ports, redirects, domains, registry, security, destination, certificates, backup, volumeBackups) go through the helper. No `resp["<kind>Id"]` indexing is left in `src/`.
- `src/icarus/commands.py`: the 12 setup create sites likewise, plus `cmd_apply` now runs the 15 reconcile steps through `_reconcile_phases`/`_run_reconcile_phases`, which catches one step's exception, keeps going, always runs the trigger, and exits 1 listing the failed steps. `SystemExit` is not caught, so a failed registry/destination connection test still aborts loudly.
- `tests/test_unit.py`: `TestCreatedRow`, `TestReconcileToleratesBooleanCreates` (parametrized boolean sweep plus the unrecoverable-id case), `TestCmdApplyReconcileFailureIsolation`.
- `tests/test_integration.py`: `_setup_router` replays the real shapes - `redirects.create` returns `json=True` and records rows served by `application.one` - with the object shape still covered by `test_repo_name_is_not_owner_repo`.
- `docs/api.md`: new "Create Response Shapes" table documenting what each create returns and which read recovers the id.

## Why this shape

One shared helper instead of per-site guards keeps the tolerance uniform and makes a future endpoint that starts answering `true` a data change at one call site. Response shapes were read from upstream handler source (v0.30.10 and v0.31.0), not the vendored OpenAPI spec, which types every create 200 as an untyped object.

## Verification

- Live, https://dokploy.xyz (v0.31.0): throwaway project, one nginx app, one redirect → `ic --env verify apply` clean, state `redirectId` matched the server row; a second apply added a redirect through the reconcile path (the reported crash site) and recorded the recovered id; `destroy` left no project behind.
- `uv run pytest tests/ -q`: 575 passed, 5 deselected. ruff check/format, markdownlint, yamllint clean.
- Landed as `fix(reconcile): guard create responses that return a bare boolean` (Refs task-031), not yet pushed.

## Risks and follow-ups

- The per-step `except Exception` in apply turns a hard abort into a printed failure plus exit 1. Real API faults are still reported per step, but a caller that only checks for a traceback will now see a non-zero exit instead.
- Id recovery relies on the natural key being unique per parent (regex, mountPath, publishedPort, username, name, prefix). Dokploy does not enforce that, so a duplicate row could adopt the wrong id; the reconcilers already treat those keys as unique for diffing.
- Not covered live: `backup.create`/`volumeBackups.create` recovery (needs a destination with credentials). Both are covered by the boolean-shape unit sweep.
<!-- SECTION:FINAL_SUMMARY:END -->
