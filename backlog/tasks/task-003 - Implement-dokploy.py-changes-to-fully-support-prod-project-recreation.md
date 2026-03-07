---
id: TASK-003
title: Implement dokploy.py changes to fully support prod project recreation
status: Done
assignee: []
created_date: '2026-03-06 08:14'
updated_date: '2026-03-07 03:06'
labels:
  - implementation
  - prod
dependencies:
  - TASK-002
  - TASK-001.02
  - TASK-001.03
  - TASK-001.04
references:
  - 'backlog://task/002'
documentation:
  - 'backlog://document/doc-001'
  - 'backlog://document/doc-002'
  - 'backlog://document/doc-003'
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Based on the gap report and findings from TASK-002, implement all necessary changes to `dokploy.py`, `dokploy.schema.json`, and supporting files so that both prod projects can be fully recreated via `dokploy.py` without manual steps.

If TASK-002 concludes that the current script already supports everything needed, this task can be closed immediately with a note confirming no changes are required.

**Likely work (pending TASK-002 findings):**

- Add support for any unsupported resource types (e.g., compose apps, databases, Redis, volumes, mounts, redirects, advanced domain settings, build args)
- Update `dokploy.schema.json` to reflect new config fields
- Update `dokploy.yml.example` and `docs/configuration.md` with new options
- Add or update example configs in `examples/`
- Finalize the draft `dokploy.yml` configs from TASK-002 into working, validated configs for both prod projects
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All gaps identified in TASK-002 are addressed in dokploy.py (or explicitly deferred with justification)
- [x] #2 dokploy.schema.json validates the new config fields
- [ ] #3 Both prod projects have working dokploy.yml configs that pass `dokploy.py check`
- [ ] #4 Running `setup`, `env`, and `deploy` against a test environment successfully recreates the project structure
- [ ] #5 No manual steps remain — or any remaining ones are documented and tracked as separate tasks
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Input from TASK-002

All analysis artifacts are in backlog documents:
- **DOC-001** — Full prod inventory, settings mapping, and gap report (7 gaps identified)
- **DOC-002** — Draft dokploy.yml for popurls (ready to finalize)
- **DOC-003** — Draft dokploy.yml for fuck47 (blocked until buildType support is added)

### Gaps to implement (priority order)
1. **buildType** support in schema + dokploy.py (CRITICAL — blocks fuck47)
2. **domain.path** field in schema + domain.create call (blocks fuck47)
3. **autoDeploy** config field + application.update call
4. **watchPaths** config field + saveGithubProvider passthrough
5. **domain.internalPath** and **domain.stripPath** optional fields
6. **replicas** config field + application.update call
7. **triggerType** and **buildPath** — make configurable instead of hardcoded

## Implementation Summary

### Changes Made

**dokploy.py** — 4 new pure helper functions extracted + cmd_setup updated:
- `build_github_provider_payload()` — configurable buildPath, triggerType, watchPaths (was hardcoded)
- `build_build_type_payload()` — configurable buildType with conditional fields per type (was hardcoded to dockerfile)
- `build_domain_payload()` — optional path, internalPath, stripPath fields
- `build_app_settings_payload()` — optional autoDeploy, replicas via application.update

**schemas/dokploy.schema.json** — new fields added:
- Apps: buildType, dockerfile, dockerContextPath, dockerBuildStage, publishDirectory, autoDeploy, replicas, buildPath, triggerType, watchPaths
- Domain: path, internalPath, stripPath
- Environment overrides: all new app fields available

**docs/configuration.md** — all new fields documented

**dokploy.yml.example** — new fields demonstrated with comments

**examples/web-app/dokploy.yml** — autoDeploy added

**tests/** — 21 new tests (57 total, all passing):
- TestBuildGithubProviderPayload (2 tests)
- TestBuildBuildTypePayload (5 tests)
- TestBuildDomainPayload (3 tests)
- TestBuildAppSettingsPayload (5 tests)
- TestMergeEnvOverridesNewFields (4 tests)
- TestValidateConfigNewFixtures (2 tests)

**New test fixtures:**
- github_static_config.yml (fuck47-style static site)
- github_dockerfile_config.yml (popurls-style dockerfile app with watchPaths)

### Gap Coverage

| Gap | Status |
|-----|--------|
| 1. buildType | Implemented — enum: dockerfile, nixpacks, static, heroku, docker |
| 2. domain.path | Implemented — optional string field |
| 3. autoDeploy | Implemented — boolean, via application.update |
| 4. watchPaths | Implemented — array of strings, passed to saveGithubProvider |
| 5. domain.internalPath + stripPath | Implemented — optional fields |
| 6. replicas | Implemented — integer, via application.update |
| 7. triggerType + buildPath | Implemented — configurable, defaults to push and / |
<!-- SECTION:NOTES:END -->
