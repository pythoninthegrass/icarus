---
id: TASK-009
title: Monitor Dokploy upstream releases via GitHub Actions
status: To Do
assignee: []
created_date: '2026-03-07 06:46'
updated_date: '2026-03-07 06:48'
labels:
  - ci
  - automation
dependencies: []
references:
  - scripts/fetch_openapi.sh
  - schemas/src/
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a GitHub Actions workflow that watches for new Dokploy releases and automatically fetches the updated OpenAPI spec.

**Trigger**: GitHub Actions schedule or `workflow_dispatch` that checks for new releases in `Dokploy/dokploy`.

**On new release**:
1. Run `scripts/fetch_openapi.sh <tag>` to download the OpenAPI spec to `schemas/src/openapi_<version>.json`
2. Open an issue in `dokploy_seed` summarizing the new release and flagging any breaking changes to validate

**Why**: Manual tracking of upstream releases is easy to miss. Automating spec fetching and issue creation ensures we stay current and catch API changes early.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GHA workflow triggers on new Dokploy releases (event-based)
- [ ] #2 Workflow runs `scripts/fetch_openapi.sh <tag>` and commits the new spec to `schemas/src/`
- [ ] #3 Workflow opens a `dokploy_seed` issue with the release tag, changelog link, and a checklist for validating breaking changes
- [ ] #4 Workflow is idempotent — re-running for an already-fetched version is a no-op
- [ ] #5 README or docs updated with workflow description
<!-- AC:END -->
