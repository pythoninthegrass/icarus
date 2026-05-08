---
id: TASK-025
title: Add backup and destination management
status: Done
assignee: []
created_date: '2026-03-23 18:05'
updated_date: '2026-03-25 20:59'
labels:
  - gap-analysis
  - new-resource
milestone: m-0
dependencies:
  - TASK-019
references:
  - main.py
  - schemas/dokploy.schema.json
priority: medium
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The TF provider manages automated database backups with cron schedules, S3-compatible destinations, and retention policies. Icarus has nothing here. This depends on database management (TASK-019) being implemented first. Add backup destinations (S3 config) and per-database backup schedules to `dokploy.yml`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Can declare backup destinations in dokploy.yml (S3 endpoint, bucket, credentials)
- [ ] #2 Can declare backup schedules per database (cron, prefix, retention count)
- [ ] #3 Backups are created during setup and reconciled on apply
<!-- AC:END -->
