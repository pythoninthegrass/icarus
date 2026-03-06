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
docs/                       # Configuration reference, API notes
examples/                   # Example configs (web-app, docker-only, minimal)
```

## Key Commands

```bash
uv run --script dokploy.py --help                       # Show usage
uv run --script dokploy.py --env prod setup             # Create project
uv run --script dokploy.py --env prod env               # Push env vars
uv run --script dokploy.py --env prod deploy            # Deploy apps
uv run --script dokploy.py --env prod status            # Check status
uv run --script dokploy.py --env prod destroy           # Tear down
```

## Linting & Formatting

- **ruff** for Python (line length 88, 4-space indent)
- **markdownlint** for Markdown (config in `.markdownlint.jsonc`)

## When Modifying

- If `dokploy.yml` structure changes: update `schemas/dokploy.schema.json`, `docs/configuration.md`, and `dokploy.yml.example`
- If API behavior changes: update `docs/api-notes.md`
- Example configs in `examples/` should validate against the schema
