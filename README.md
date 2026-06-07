# icarus

<p align="center">
  <img src="logo.png" alt="icarus logo" width="300">
  <br>
  <sub>Image credit: <a href="https://www.history-for-kids.com/icarus.html">History for Kids</a></sub>
</p>

Deployment tool for [Dokploy](https://dokploy.com). Define your project's apps, domains, deploy order, and environment overrides in a single `dokploy.yml` — the tool handles the Dokploy API calls (cf. IaC).

**Table of Contents**

* [icarus](#icarus)
  * [Prerequisites](#prerequisites)
  * [Install](#install)
  * [Quick Start](#quick-start)
    * [Creating from the command line](#creating-from-the-command-line)
  * [Commands](#commands)
  * [Environment Selection](#environment-selection)
  * [Configuration](#configuration)
  * [Multi-Environment Support](#multi-environment-support)
  * [Env Filtering](#env-filtering)
  * [State Files](#state-files)
  * [Examples](#examples)
  * [Adding to an Existing Project](#adding-to-an-existing-project)
  * [CI Workflows](#ci-workflows)
  * [API Notes](#api-notes)
  * [Contributing](#contributing)
  * [Security](#security)
  * [License](#license)

## Prerequisites

* A running [Dokploy](https://dokploy.com) instance
* [uv](https://docs.astral.sh/uv/)
* A Dokploy API key (generated in Dokploy UI > Settings > API)

## Install

### Standalone (no install)

Copy `main.py` to the target machine and run directly.
Dependencies are resolved automatically by `uv` via
[PEP 723](https://peps.python.org/pep-0723/) inline metadata.

```bash
# Run from the repo
./main.py list

# Or explicitly with uv
uv run --script main.py --help
```

### Install from git

```bash
# One-off execution
uvx --from git+https://github.com/pythoninthegrass/icarus.git ic --help

# Persistent install
uv tool install git+https://github.com/pythoninthegrass/icarus.git
ic --help
```

## Quick Start

1. Click **Use this template** > **Create a new repository** at the top of this page (or use the [GitHub CLI](#creating-from-the-command-line)).

2. Clone your new repo and create your `dokploy.yml` (see `dokploy.yml.example` or the `examples/` directory).

3. Set environment variables:

    ```bash
    export DOKPLOY_URL=https://dokploy.example.com
    export DOKPLOY_API_KEY=your-api-key
    ```

    Or add them to your project's `.env` file (see `.env.example`).

4. Run:

    ```bash
    ic --env prod setup     # Create project + apps + providers + domains
    ic --env prod env       # Push filtered .env to env_targets + per-app env
    ic --env prod apply     # Full pipeline: check, setup, env, trigger
    ic --env prod status    # Show status of all apps
    ic --env prod destroy   # Delete project + all apps
    ```

### Creating from the command line

```bash
gh repo create my-project --template pythoninthegrass/icarus --private --clone
cd my-project
```

## Commands

| Command   | Description                                                                                     |
| --------- | ----------------------------------------------------------------------------------------------- |
| `check`   | Verify Dokploy connectivity and configuration                                                   |
| `setup`   | Create Dokploy project, apps, configure providers (Docker/GitHub), set commands, create domains |
| `env`     | Push filtered `.env` to `env_targets` apps + per-app custom env vars                            |
| `plan`    | Preview changes `apply` would make without executing them (dry run)                             |
| `apply`   | Full pipeline: check, setup, env, trigger deploys in `deploy_order` wave sequence               |
| `status`  | Show application status for all apps in the project                                             |
| `clean`   | Remove stale Traefik rules and Docker artifacts                                                 |
| `destroy` | Delete the Dokploy project (cascades to all apps) and remove state file                         |
| `logs`    | Tail container logs (`-f` to follow, `-n` for line count, `--exited` for stopped containers)    |
| `exec`    | Open an interactive shell or run a command in a running container                               |

## Environment Selection

The `--env` flag is optional and defaults to `dev`. It can also be set via the `DOKPLOY_ENV` environment variable (in `.env` or exported):

```bash
# Explicit flag (highest priority)
ic --env prod status

# Via environment variable
export DOKPLOY_ENV=prod
ic status

# No flag, no variable -> defaults to 'dev'
ic status

# Standalone mode
uv run --script main.py --env prod status
```

Resolution order: `--env` flag > `DOKPLOY_ENV` (from `.env` or environment) > `dev`.

## Configuration

All configuration lives in `dokploy.yml`. See [docs/configuration.md](docs/configuration.md) for the full reference.

The file is validated by `schemas/dokploy.schema.json`. Add this directive at the top of your `dokploy.yml` for IDE autocomplete and validation:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/pythoninthegrass/icarus/main/schemas/dokploy.schema.json
```

## Multi-Environment Support

Each deployment targets a named environment (e.g. `prod`, `dev`). The environment determines:

1. **Project name**: base `project.name` is suffixed with `-<env>` (e.g. `my-app-prod`)
2. **State file**: each environment gets its own `.dokploy-state/<env>.json`
3. **Config overrides**: the `environments` section in `dokploy.yml` can override `github` settings and per-app properties

<details>
<summary><strong>Config Merging</strong></summary>

The `environments.<env>` section is merged into the base config before any command runs:

* `environments.<env>.github` — shallow-merges into the base `github` section (e.g. override `branch` per environment)
* `environments.<env>.apps.<name>` — shallow-merges into the matching base app definition (can override `command`, `env`, `dockerImage`, `domain`)

> [!IMPORTANT]
> Structural properties (`name`, `source`) cannot be overridden per-environment since they define the app's identity.

</details>

<details>
<summary><strong>Config File Discovery</strong></summary>

The tool walks upward from the current working directory looking for `dokploy.yml`. This means it works from any subdirectory within a project that has a `dokploy.yml` at its root.

</details>

## Env Filtering

When pushing `.env` to `env_targets`, comments, blank lines, and lines matching `DEFAULT_ENV_EXCLUDE_PREFIXES` are stripped. Built-in excluded prefixes:

* `COMPOSE_`, `CONTAINER_NAME`, `DOPPLER_`, `PGDATA`, `POSTGRES_VERSION`, `DOKPLOY_`, `TASK_X_`

> [!TIP]
> Extend with `ENV_EXCLUDE_PREFIXES` in your `.env` (comma-separated).

## State Files

`.dokploy-state/<env>.json` files contain Dokploy resource IDs (project ID, app IDs, app names). They contain **no secrets** and should be committed to version control.

## Examples

See the `examples/` directory:

* [`web-app/`](examples/web-app/dokploy.yml) — GitHub-sourced web + worker + redis + flower monitor
* [`docker-only/`](examples/docker-only/dokploy.yml) — All Docker images, no GitHub source
* [`minimal/`](examples/minimal/dokploy.yml) — Single Docker app, simplest valid config

## Adding to an Existing Project

Install `ic` globally:

```bash
uv tool install git+https://github.com/pythoninthegrass/icarus.git
```

Then in your project directory:

1. Create `dokploy.yml` based on `dokploy.yml.example` (schema is fetched from GitHub automatically)
2. Create `.dokploy-state/` directory (add a `.gitkeep`)
3. Add `DOKPLOY_URL` and `DOKPLOY_API_KEY` to your `.env`
4. Run `ic --env prod setup`

> [!WARNING]
> The `destroy` command is irreversible — it deletes the entire Dokploy project and all associated apps. The local state file is also removed.

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

## Development

```bash
# Install python, ruff, and uv via mise
mise install

# Install project dependencies
uv sync --all-extras

# Lint
ruff format --check --diff .
ruff check .

# Format
ruff format .
```

## CI Workflows

| Workflow                                                 | Trigger                   | Description                                                                           |
| -------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------- |
| [E2E Tests](.github/workflows/e2e.yml)                   | Push/PR to `main`         | Runs end-to-end test suite against a live Dokploy instance                            |
| [Release Please](.github/workflows/release-please.yml)   | Push to `main`            | Automates version bumps and changelog generation                                      |
| [Monitor Dokploy](.github/workflows/monitor-dokploy.yml) | Daily (8 AM UTC) / manual | Checks for new Dokploy releases, fetches the OpenAPI spec, and opens a tracking issue |

The monitor workflow can also be triggered manually via `workflow_dispatch` with an optional tag input (e.g., `v0.28.8`). If the spec for a given version already exists in `schemas/src/`, the run is a no-op.

## API Notes

See [docs/api-notes.md](docs/api-notes.md) for Dokploy API quirks and gotchas.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md)

## License

[Unlicense](LICENSE)
