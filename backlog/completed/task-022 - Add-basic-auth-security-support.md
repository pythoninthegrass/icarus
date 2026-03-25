---
id: TASK-022
title: Add basic auth / security support
status: Done
assignee: []
created_date: '2026-03-23 18:05'
updated_date: '2026-03-25 18:03'
labels:
  - gap-analysis
  - new-resource
milestone: m-0
dependencies: []
references:
  - main.py
  - schemas/dokploy.schema.json
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The TF provider can configure basic authentication (username/password) on applications. Icarus cannot. Add `security` config to app definitions for basic auth protection. Useful for staging environments and admin panels.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Apps can declare basic auth in dokploy.yml (username, password)
- [x] #2 Security config is applied during setup
- [x] #3 Security config is reconciled on apply/redeploy
<!-- AC:END -->
