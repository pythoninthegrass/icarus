# AGENTS.md

## Project Overview

`dokploy_seed` — a standalone, config-driven deployment script for [Dokploy](https://dokploy.com). Designed to be copied into any project repo. Define apps, domains, deploy order, and environment overrides in `dokploy.yml`; the script handles all Dokploy API calls.

## Tech Stack

- **Python 3.13** (PEP 723 inline script — no `pyproject.toml`, no virtualenv)
- **uv** for script execution (`uv run --script`)
- Dependencies: `httpx`, `python-decouple`, `pyyaml`

## Project Structure

```text
dokploy.py                  # The deployment script (top-level)
dokploy.yml.example         # Annotated starter config
schemas/dokploy.schema.json # JSON Schema for dokploy.yml
.dokploy-state/             # State files (resource IDs, committed)
docs/                       # Configuration reference, API notes, testing guide
examples/                   # Example configs (web-app, docker-only, minimal)
tests/                      # Pytest suite (see docs/testing.md)
  fixtures/                 # YAML-backed test data
```

## Key Commands

```bash
uv run --script dokploy.py --help                       # Show usage
uv run --script dokploy.py check                        # Pre-flight checks
uv run --script dokploy.py --env prod setup             # Create project
uv run --script dokploy.py --env prod env               # Push env vars
uv run --script dokploy.py --env prod deploy            # Deploy apps
uv run --script dokploy.py --env prod status            # Check status
uv run --script dokploy.py --env prod destroy           # Tear down
```

## Testing

```bash
uv run pytest tests/ -v               # all tests, verbose
uv run pytest tests/ -x               # stop on first failure
uv run pytest tests/ -k "filter_env"   # keyword filter
uv run pytest tests/ -m unit          # run by marker
```

See [docs/testing.md](docs/testing.md) for fixture architecture, markers, coverage, and how to add new fixtures.

## Linting & Formatting

- **ruff** for Python (line length 88, 4-space indent)
- **markdownlint** for Markdown (config in `.markdownlint.jsonc`)

## When Modifying

- If `dokploy.yml` structure changes: update `schemas/dokploy.schema.json`, `docs/configuration.md`, and `dokploy.yml.example`
- If API behavior changes: update `docs/api-notes.md`
- If test data changes: update YAML files in `tests/fixtures/`, not inline dicts
- Example configs in `examples/` should validate against the schema
- NEVER add section comments (e.g. `# --- Section Name ---`, `# == Foo ==`) to any code; use class/function structure and docstrings for organization

## Worktrunk (worktree cleanup)

**CRITICAL**: Before running `wt remove`, you MUST first change your working directory to the main repo:

```bash
cd dokploy_seed && wt remove <name>
```

If you skip this, `wt remove` deletes your CWD and **every subsequent Bash call will fail irrecoverably**. There is no way to fix a broken CWD in a Claude Code session — the entire session is bricked.

Shell integration (`wt` as a shell function) does NOT help here because each Bash tool call is an independent shell — the `cd` side-effect cannot persist.

## Context7

Always use Context7 MCP when I need library/API documentation, code generation, setup or configuration steps without me having to explicitly ask.

### Libraries

- dokploy/website
- hypothesisworks/hypothesis
- jdx/mise
- max-sixty/worktrunk
- mrlesk/backlog.md
- websites/taskfile_dev

<!-- BACKLOG.MD MCP GUIDELINES START -->

<CRITICAL_INSTRUCTION>

## BACKLOG WORKFLOW INSTRUCTIONS

This project uses Backlog.md MCP for all task and project management.

**CRITICAL RESOURCE**: Read `backlog://workflow/overview` to understand when and how to use Backlog for this project.

- **First time working here?** Read the overview resource IMMEDIATELY to learn the workflow
- **Already familiar?** You should have the overview cached ("## Backlog.md Overview (MCP)")
- **When to read it**: BEFORE creating tasks, or when you're unsure whether to track work

### Key MCP Commands

| Command | Purpose |
|---------|---------|
| `task_create` | Create a new task (status defaults to "To Do") |
| `task_edit` | Edit metadata, check ACs, update notes, change status |
| `task_view` | View full task details |
| `task_search` | Find tasks by keyword |
| `task_list` | List tasks with optional filters |
| `task_complete` | **Moves task to `backlog/completed/`** — only use for cleanup, not for marking done |

### Task Lifecycle

1. **Create**: `task_create` — new task in `backlog/tasks/`
2. **Start**: `task_edit(status: "In Progress")` — mark as active
3. **Done**: `task_edit(status: "Done")` — mark finished, stays in `backlog/tasks/` (visible on kanban)
4. **Archive**: `task_complete` — moves to `backlog/completed/` (use only when explicitly cleaning up)

**IMPORTANT**: Use `task_edit(status: "Done")` to mark tasks as done. Do NOT use `task_complete` unless the user explicitly asks to archive/clean up — it removes the task from the kanban.

The overview resource contains additional detail on decision frameworks, search-first workflow, and guides for task creation, execution, and completion.

</CRITICAL_INSTRUCTION>

<!-- BACKLOG.MD MCP GUIDELINES END -->
