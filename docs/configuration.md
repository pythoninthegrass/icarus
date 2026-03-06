# Configuration Reference

`dokploy.yml` is validated by `schemas/dokploy.schema.json`. Add this directive at the top for IDE autocomplete:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/pythoninthegrass/dokploy_seed/main/schemas/dokploy.schema.json
```

## Top-Level Keys

| Key | Required | Description |
|-----|----------|-------------|
| `project` | yes | Project metadata, env targets, deploy order |
| `github` | if github apps | GitHub defaults for all github-sourced apps |
| `environments` | no | Per-environment overrides |
| `apps` | yes | List of app definitions |

## `project`

| Key | Required | Description |
|-----|----------|-------------|
| `project.name` | yes | Dokploy project name (suffixed with `-<env>` at runtime) |
| `project.description` | yes | Project description |
| `project.env_targets` | no | App names that receive the filtered `.env` file |
| `project.deploy_order` | no | Deploy waves — outer list is sequential, inner lists are parallel |

## `github`

Omit this entire section for Docker-only projects.

| Key | Required | Description |
|-----|----------|-------------|
| `github.owner` | yes | GitHub org or user |
| `github.repository` | yes | Repo name (not `owner/repo` — Dokploy prepends owner) |
| `github.branch` | yes | Branch to deploy from |

## `environments`

Per-environment overrides merged into the base config before any command runs.

| Key | Required | Description |
|-----|----------|-------------|
| `environments.<env>.github` | no | Override `github` settings for this environment |
| `environments.<env>.apps.<name>` | no | Override app properties (`command`, `env`, `dockerImage`, `domain`) |

### Merging Semantics

- `github` overrides: shallow merge into base `github` section
- `apps.<name>` overrides: shallow merge into the matching base app definition
- Structural properties (`name`, `source`) cannot be overridden — they define the app's identity

## `apps`

| Key | Required | Description |
|-----|----------|-------------|
| `apps[].name` | yes | Unique app name within the project |
| `apps[].source` | yes | `docker` or `github` |
| `apps[].dockerImage` | if docker | Docker image reference |
| `apps[].command` | no | Command override, supports `{app_name}` refs |
| `apps[].env` | no | Per-app env vars (not the project `.env`), supports `{app_name}` refs |
| `apps[].domain` | no | Single domain object or list of domain objects |

### Domain Object

| Key | Required | Description |
|-----|----------|-------------|
| `domain.host` | yes | Domain hostname |
| `domain.port` | yes | Container port to expose |
| `domain.https` | yes | Enable HTTPS |
| `domain.certificateType` | yes | `none` or `letsencrypt` |

## `{app_name}` Resolution

In `command` and `env` fields, `{app_name}` placeholders are replaced with the Dokploy-assigned `appName` from the state file. This allows apps to reference each other's internal Docker network hostnames.

Example: `redis://{redis}:6379/0` resolves to `redis://redis-abcdef:6379/0` (where `redis-abcdef` is the Dokploy appName).

Resolution happens during `setup` (for commands) and `env` (for environment variables).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DOKPLOY_URL` | yes | — | Dokploy server URL |
| `DOKPLOY_API_KEY` | yes | — | API key for authentication |
| `DOKPLOY_ENV` | no | `dev` | Target environment (alternative to `--env` flag) |
| `ENV_EXCLUDE_PREFIXES` | no | — | Extra env var prefixes to exclude when pushing `.env` |

Resolution order: `--env` flag > `DOKPLOY_ENV` (from `.env` or environment) > `dev`.

## Schema Directive

The `# yaml-language-server` directive at the top of `dokploy.yml` enables IDE features:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/pythoninthegrass/dokploy_seed/main/schemas/dokploy.schema.json
```

This works in VS Code (with the YAML extension), JetBrains IDEs, and other editors that support the yaml-language-server protocol. You can also use a local relative path (`$schema=schemas/dokploy.schema.json`) if you have a copy of the schema in your repo.
