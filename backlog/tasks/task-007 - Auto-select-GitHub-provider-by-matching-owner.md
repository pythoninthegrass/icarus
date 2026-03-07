---
id: TASK-007
title: Auto-select GitHub provider by matching owner
status: Done
assignee: []
created_date: '2026-03-07 04:06'
updated_date: '2026-03-07 05:06'
labels: []
dependencies: []
references:
  - .claude/plans/binary-yawning-cat.md
  - dokploy.py
  - tests/test_integration.py
  - docs/api-notes.md
priority: high
ordinal: 500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`dokploy.py` always picks `providers[0]` from `github.githubProviders`. When multiple providers exist (e.g. personal account + org), it picks the wrong one.

Query each provider's repos via `github.getGithubRepositories?githubId=X` and match `owner.login` against `github.owner` from `dokploy.yml`. Pick the provider whose repos include the configured owner.

See plan at `.claude/plans/binary-yawning-cat.md` for full implementation details.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 When multiple GitHub providers exist, the correct one is selected based on which provider has access to repos owned by the configured `github.owner`
- [x] #2 Clear error message when no provider has access to the configured owner
- [x] #3 Existing tests updated with mock for `github.getGithubRepositories`
- [x] #4 New test covers provider mismatch scenario
- [x] #5 `docs/api-notes.md` documents the `getGithubRepositories` endpoint
<!-- AC:END -->
