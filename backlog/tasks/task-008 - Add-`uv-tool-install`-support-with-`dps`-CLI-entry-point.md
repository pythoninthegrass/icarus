---
id: TASK-008
title: Add `uv tool install` support with `dps` CLI entry point
status: In Progress
assignee: []
created_date: '2026-03-07 06:42'
updated_date: '2026-03-07 06:43'
labels:
  - enhancement
  - cli
dependencies: []
references:
  - dokploy.py
  - AGENTS.md
  - README.md
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add `pyproject.toml` with `uv_build` backend and `dps` entry point so the project can be installed globally via `uv tool install git+https://github.com/pythoninthegrass/dokploy_seed`. Change path resolution from `__file__`-based to `cwd`-based so the tool finds `dokploy.yml` and `.dokploy-state/` relative to where it's invoked. Update all project docs. Keep backward compat with `uv run --script dokploy.py`.

## Implementation Details

1. **Add `pyproject.toml`** at repo root:
   - `name = "dokploy-seed"`
   - `[project.scripts] dps = "dokploy:main"`
   - `build-system`: `uv_build`
   - Dependencies mirrored from PEP 723 inline metadata in `dokploy.py`
   - `requires-python = ">=3.13,<3.14"`

2. **Change `find_repo_root()`** (`dokploy.py:37-48`):
   - Replace `Path(__file__).resolve().parent` with `Path.cwd()`
   - Both modes work: `uv run --script` users are already in the repo; `dps` users are in their project dir

3. **Change `cmd_check` call** (`dokploy.py:714`):
   - Replace `Path(__file__).resolve().parent` with `Path.cwd()`

4. **Keep PEP 723 inline metadata** in `dokploy.py` for `uv run --script` backward compat

5. **Update AGENTS.md**:
   - Line 5: Overview — mention installable via `uv tool install` in addition to "copied into any project repo"
   - Lines 9-11: Tech stack — note dual mode (PEP 723 inline script + `pyproject.toml` for tool install)
   - Lines 15-24: Project structure — add `pyproject.toml` entry
   - Lines 28-36: Key Commands — add `dps` commands alongside `uv run --script` examples

6. **Update README.md**:
   - Line 27: Prerequisites — mention `uv tool install` as alternative installation method
   - Lines 30-53: Quick Start — add installation via `uv tool install git+https://github.com/pythoninthegrass/dokploy_seed`, show `dps` commands
   - Lines 76-86: Environment Selection — show `dps` examples alongside `uv run --script`
   - Lines 123-127: Config File Discovery details — update to reflect cwd-based resolution
   - Lines 151-159: Adding to an Existing Project — add `uv tool install` as a simpler alternative to copying files

7. **docs/ files** — no changes needed (api-notes.md, configuration.md, testing.md don't reference invocation patterns)

## Key Files

- `dokploy.py` — `find_repo_root()` (line 37), `main()` (line 698)
- `pyproject.toml` — new file
- `AGENTS.md` — overview, tech stack, structure, key commands
- `README.md` — prerequisites, quick start, environment selection, config discovery, adding to existing project
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `uv tool install git+https://github.com/pythoninthegrass/dokploy_seed` succeeds
- [ ] #2 `dps --help` works from any directory
- [ ] #3 `dps --env prod setup` works from a directory containing `dokploy.yml`
- [ ] #4 `uv run --script dokploy.py setup` still works (backward compat)
- [ ] #5 `.dokploy-state/` and `dokploy.yml` resolved from cwd
- [ ] #6 AGENTS.md updated with dual-mode info and `dps` commands
- [ ] #7 README.md updated with installation, quick start, and usage for `dps`
<!-- AC:END -->
