# dokploy_seed

Deployment script for [Dokploy](https://dokploy.com). Define your project's apps, domains, deploy order, and environment overrides in a single `dokploy.yml` — the script handles the Dokploy API calls (cf. IaC).

**Table of Contents**

* [dokploy\_seed](#dokploy_seed)
  * [Prerequisites](#prerequisites)
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
  * [API Notes](#api-notes)
  * [Contributing](#contributing)
  * [Security](#security)
  * [License](#license)

## Prerequisites

* A running [Dokploy](https://dokploy.com) instance
* [uv](https://docs.astral.sh/uv/) (the script runs as a [PEP 723](https://peps.python.org/pep-0723/) inline script via `uv run --script`)
* A Dokploy API key (generated in Dokploy UI > Settings > API)

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
    ./dokploy.py --env prod setup     # Create project + apps + providers + domains
    ./dokploy.py --env prod env       # Push filtered .env to env_targets + per-app env
    ./dokploy.py --env prod deploy    # Deploy apps in wave order
    ./dokploy.py --env prod status    # Show status of all apps
    ./dokploy.py --env prod destroy   # Delete project + all apps
    ```

### Creating from the command line

```bash
gh repo create my-project --template pythoninthegrass/dokploy_seed --private --clone
cd my-project
```

## Commands

| Command   | Description                                                                                      |
|-----------|--------------------------------------------------------------------------------------------------|
| `setup`   | Create Dokploy project, apps, configure providers (Docker/GitHub), set commands, create domains  |
| `env`     | Push filtered `.env` to `env_targets` apps + per-app custom env vars                             |
| `deploy`  | Trigger deploys in `deploy_order` wave sequence                                                  |
| `status`  | Show application status for all apps in the project                                              |
| `destroy` | Delete the Dokploy project (cascades to all apps) and remove state file                          |

## Environment Selection

The `--env` flag is optional and defaults to `dev`. It can also be set via the `DOKPLOY_ENV` environment variable (in `.env` or exported):

```bash
# Explicit flag (highest priority)
uv run --script dokploy.py --env prod status

# Via environment variable
export DOKPLOY_ENV=prod
uv run --script dokploy.py status

# No flag, no variable → defaults to 'dev'
uv run --script dokploy.py status
```

Resolution order: `--env` flag > `DOKPLOY_ENV` (from `.env` or environment) > `dev`.

## Configuration

All configuration lives in `dokploy.yml`. See [docs/configuration.md](docs/configuration.md) for the full reference.

The file is validated by `schemas/dokploy.schema.json`. Add this directive at the top of your `dokploy.yml` for IDE autocomplete and validation:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/pythoninthegrass/dokploy_seed/main/schemas/dokploy.schema.json
```

## Multi-Environment Support

Each deployment targets a named environment (e.g. `prod`, `dev`). The environment determines:

1. **Project name**: base `project.name` is suffixed with `-<env>` (e.g. `my-app-prod`)
2. **State file**: each environment gets its own `.dokploy-state/<env>.json`
3. **Config overrides**: the `environments` section in `dokploy.yml` can override `github` settings and per-app properties

<!-- markdownlint-disable MD033 -->
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

The script walks upward from its own location looking for `dokploy.yml`. This means it works whether placed at the repo root or in a `scripts/` subdirectory.

</details>
<!-- markdownlint-enable MD033 -->

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

If you already have a repo and prefer to copy the files instead of using the template:

1. Copy `dokploy.py` to your repo root (or `scripts/`)
2. Create `dokploy.yml` based on `dokploy.yml.example` (schema is fetched from GitHub automatically)
3. Create `.dokploy-state/` directory (add a `.gitkeep`)
4. Add `DOKPLOY_URL` and `DOKPLOY_API_KEY` to your `.env`
5. Run `./dokploy.py --env prod setup`

> [!WARNING]
> The `destroy` command is irreversible — it deletes the entire Dokploy project and all associated apps. The local state file is also removed.

## API Notes

See [docs/api-notes.md](docs/api-notes.md) for Dokploy API quirks and gotchas.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md)

## License

[Unlicense](LICENSE)
