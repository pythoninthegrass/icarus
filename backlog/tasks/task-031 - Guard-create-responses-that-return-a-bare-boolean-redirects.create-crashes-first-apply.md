---
id: TASK-031
title: >-
  Guard create responses that return a bare boolean (redirects.create crashes
  first apply)
status: To Do
assignee: []
created_date: '2026-11-08T22:42:00Z'
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
- [ ] #1 Creating a redirect through ic apply succeeds against Dokploy v0.31.0 with no traceback, and the state file records the real redirectId
- [ ] #2 Every create path tolerates a non-object response by re-reading the parent resource for the id, or fails with an error naming the endpoint: the ten reconcile.py sites and the commands.py setup equivalents
- [ ] #3 Tests cover the boolean-response shape, not only the object shape the current integration mock returns, so the regression cannot return
- [ ] #4 A failing reconcile step no longer silently skips the remaining phases and the deploy trigger: either all phases run and the command exits non-zero, or it aborts before any server mutation
- [ ] #5 The pytest suite passes and the change lands as a conventional commit with no attribution
<!-- AC:END -->
