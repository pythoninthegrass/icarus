# AGENTS.md

## Project Overview

`icarus` — a config-driven deployment tool for [Dokploy](https://dokploy.com). Installable via `uv tool install`. Define apps, domains, deploy order, and environment overrides in `dokploy.yml`; the tool handles all Dokploy API calls.

## Tech Stack

- **Python 3.13** — `uv_build` packaging for tool install
- **uv** for distribution (`uv tool install`)
- Dependencies: `docker[ssh]`, `httpx`, `python-decouple`, `pyyaml`

## Project Structure

```text
main.py                     # Thin CLI router (imports from icarus package)
src/icarus/
  __init__.py               # Re-exports all public symbols (FastAPI-style)
  cli.py                    # argparse + match/case dispatch (main entry point)
  config.py                 # Config singleton, find_repo_root
  schema.py                 # dokploy.yml loading, validation, merging
  env.py                    # Env var filtering, resolve_refs
  client.py                 # DokployClient, state load/save
  payloads.py               # Payload builders, DB constants, compose helpers
  reconcile.py              # Reconciliation logic for redeploy
  plan.py                   # Plan/diff logic, cmd_plan
  ssh.py                    # SSH/Docker/Traefik/container helpers
  commands.py               # All cmd_* command implementations
pyproject.toml              # uv_build backend + ic entry point
dokploy.yml.example         # Annotated starter config
schemas/dokploy.schema.json # JSON Schema for dokploy.yml
.dokploy-state/             # State files (resource IDs, committed)
docs/                       # Configuration reference, API notes, testing guide
examples/                   # Example configs (web-app, docker-only, minimal)
tests/                      # Pytest suite (see docs/testing.md)
  fixtures/                 # YAML-backed test data
```

## Key Commands

### Installed via uv tool

```bash
ic --help                                # Show usage
ic check                                 # Pre-flight checks
ic --env prod setup                      # Create project
ic --env prod env                        # Push env vars
ic --env prod --env-file .env.prod env   # Push from alternate .env file
ic --env prod plan                       # Preview what apply would change (dry run)
ic --env prod apply                      # Full pipeline: check, setup, env, trigger
ic --env prod status                     # Check status
ic --env prod clean                      # Remove stale Traefik/Docker artifacts
ic --env prod destroy                    # Tear down
ic --env prod logs django                # Tail 100 lines of container logs
ic --env prod logs django -f             # Follow log output
ic --env prod logs django -n 500         # Last 500 lines
ic --env prod logs django --exited       # Pick from exited containers
ic --env prod exec django                # Interactive shell (sh)
ic --env prod exec django -- python manage.py shell  # Run command
```

### Reinstall after changes

```bash
uv tool install ~/git/icarus --force --reinstall
```

### Development (no install)

Make sure to run `uv sync --all-extras` to get production and development dependencies first.

```bash
uv run python main.py --help              # Show usage
uv run python main.py check               # Pre-flight checks
uv run python main.py --env prod setup    # Create project
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
- **yamllint** for YAML (config in `.yamllint`)

Tools managed by mise (prek, python, uv, yamllint, etc.) must be invoked via `mise exec --`:

```bash
mise exec -- yamllint -c .yamllint .
```

### Markdown Style

- Do NOT use em dashes (`—`) in markdown files; use a hyphen (`-`) or rephrase. Em dashes render wider than their character count in many fonts/tools, breaking table column alignment even when the underlying text is character-aligned.
- Do NOT hard-wrap prose across multiple source lines (e.g. breaking one sentence into 3 lines). Write each paragraph as a single source line; `MD013` (line length) is disabled in `.markdownlint.jsonc`, so there's no need to wrap.
- markdownlint enforces aligned table columns (MD060). When writing markdown tables, generate them programmatically to guarantee pipe alignment:

```python
rows = [["Header1", "Header2"], ["cell", "cell"]]
widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
def fmt(row):
    return "| " + " | ".join(c.ljust(w) for c, w in zip(row, widths)) + " |"
print(fmt(rows[0]))
print("| " + " | ".join("-" * w for w in widths) + " |")
for row in rows[1:]:
    print(fmt(row))
```

Do NOT hand-align tables — column math is error-prone and markdownlint rejects even single-character misalignment.

## When Modifying

- If `dokploy.yml` structure changes: update `schemas/dokploy.schema.json`, `docs/configuration.md`, and `dokploy.yml.example`
- If API behavior changes: update `docs/api.md`
- If test data changes: update YAML files in `tests/fixtures/`, not inline dicts
- Example configs in `examples/` should validate against the schema
- NEVER add section comments (e.g. `# --- Section Name ---`, `# == Foo ==`, `# -----...-----`) to any code unless explicitly asked; use class/function structure and docstrings for organization
- NEVER create new test files; add tests to the existing files in `tests/` (e.g. `test_unit.py`, `test_integration.py`, `test_e2e.py`, `test_property.py`)

## Version Control

When a commit resolves a GitHub issue and is pushed directly to `main` (no PR), add a closing trailer so GitHub auto-closes the issue:

```text
feat(compose): support sourceType: github for compose apps

...body...

Closes #18
```

Use `Closes #N` for issues that are fully resolved by the commit. Use `Refs #N` when the commit is related but does not fully close the issue.

## Worktrunk (worktree cleanup)

**CRITICAL**: Before running `wt remove`, you MUST first change your working directory to the main repo:

```bash
cd icarus && wt remove <name>
```

If you skip this, `wt remove` deletes your CWD and **every subsequent Bash call will fail irrecoverably**. There is no way to fix a broken CWD in a Claude Code session — the entire session is bricked.

Shell integration (`wt` as a shell function) does NOT help here because each Bash tool call is an independent shell — the `cd` side-effect cannot persist.

## OpenAPI Schema

Fetch the Dokploy OpenAPI schema from the upstream GitHub repo using `scripts/fetch_openapi.sh`. Schemas are stored in `schemas/src/` and named by version (e.g. `openapi_0.28.8.json`).

```bash
./scripts/fetch_openapi.sh          # latest release
./scripts/fetch_openapi.sh v0.28.8  # specific tag
```

For older Dokploy versions not available on GitHub (e.g. v0.25.6), pull the schema from the live server's OpenAPI endpoint:

```bash
curl -s -H "x-api-key: $(rg '^DOKPLOY_API_KEY=' .env | cut -d= -f2)" \
  "$(rg '^DOKPLOY_URL=' .env | cut -d= -f2)/api/settings.getOpenApiDocument" \
  | jq . > schemas/src/openapi_<version>.json
```

## Ad-hoc API Calls

Query the live Dokploy API from a local `.env` file:

```bash
curl -s -H "x-api-key: $(rg '^DOKPLOY_API_KEY=' .env | cut -d= -f2)" \
  "$(rg '^DOKPLOY_URL=' .env | cut -d= -f2)/api/<endpoint>" | jq .
```

Example — list all API paths:

```bash
curl -s -H "x-api-key: $(rg '^DOKPLOY_API_KEY=' .env | cut -d= -f2)" \
  "$(rg '^DOKPLOY_URL=' .env | cut -d= -f2)/api/settings.getOpenApiDocument" \
  | jq '.paths | keys[]'
```

## Context7

Always use Context7 MCP when I need library/API documentation, code generation, setup or configuration steps without me having to explicitly ask.

### Libraries

- astral-sh/uv
- astral-sh/ruff
- docker/docker-py
- dokploy/website
- hbnetwork/python-decouple
- hypothesisworks/hypothesis
- jdx/mise
- max-sixty/worktrunk
- mrlesk/backlog.md
- websites/orbstack_dev
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

| Command         | Purpose                                                                          |
|-----------------|----------------------------------------------------------------------------------|
| `task_create`   | Create a new task (status defaults to "To Do")                                   |
| `task_edit`     | Edit metadata, check ACs, update notes, change status                            |
| `task_view`     | View full task details                                                           |
| `task_search`   | Find tasks by keyword                                                            |
| `task_list`     | List tasks with optional filters                                                 |
| `task_complete` | **Moves task to `backlog/completed/`** — only use for cleanup, not marking done  |

### Task Lifecycle

1. **Create**: `task_create` — new task in `backlog/tasks/`
2. **Start**: `task_edit(status: "In Progress")` — mark as active
3. **Done**: `task_edit(status: "Done")` — mark finished, stays in `backlog/tasks/` (visible on kanban)
4. **Archive**: `task_complete` — moves to `backlog/completed/` (use only when explicitly cleaning up)

**IMPORTANT**: Use `task_edit(status: "Done")` to mark tasks as done. Do NOT use `task_complete` unless the user explicitly asks to archive/clean up — it removes the task from the kanban.

The overview resource contains additional detail on decision frameworks, search-first workflow, and guides for task creation, execution, and completion.

</CRITICAL_INSTRUCTION>

<!-- BACKLOG.MD MCP GUIDELINES END -->
