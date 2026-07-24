# Configuration Reference

`dokploy.yml` is validated by `schemas/dokploy.schema.json`. Add this directive at the top for IDE autocomplete:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/pythoninthegrass/icarus/main/schemas/dokploy.schema.json
```

## Top-Level Keys

| Key            | Required       | Description                                                         |
| -------------- | -------------- | ------------------------------------------------------------------- |
| `project`      | yes            | Project metadata, env targets, deploy order                         |
| `github`       | if github apps | GitHub defaults for all github-sourced apps                         |
| `environments` | no             | Per-environment overrides                                           |
| `apps`         | yes            | List of app definitions                                             |
| `database`     | no             | List of database resources (postgres, mysql, mariadb, mongo, redis) |

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
| `environments.<env>.apps.<name>` | no | Override app properties (see below) |

### Overridable App Properties

All per-app fields can be overridden per environment: `command`, `env`, `dockerImage`, `domain`, `buildType`, `dockerfile`, `dockerContextPath`, `dockerBuildStage`, `publishDirectory`, `autoDeploy`, `replicas`, `buildPath`, `triggerType`, `watchPaths`, `create_env_file`, `ports`, `schedules`.

### Merging Semantics

- `github` overrides: shallow merge into base `github` section
- `apps.<name>` overrides: shallow merge into the matching base app definition
- Structural properties (`name`, `source`) cannot be overridden — they define the app's identity

## `apps`

| Key | Required | Description |
|-----|----------|-------------|
| `apps[].name` | yes | Unique app name within the project |
| `apps[].source` | yes | `docker`, `github`, or `compose` |
| `apps[].dockerImage` | if docker | Docker image reference |
| `apps[].command` | no | Command override, supports `{app_name}` refs |
| `apps[].env` | no | Per-app env vars (not the project `.env`), supports `{app_name}` refs |
| `apps[].domain` | no | Single domain object or list of domain objects |
| `apps[].buildType` | no | Build type: `dockerfile` (default), `nixpacks`, `static`, `heroku_buildpacks`, `paketo_buildpacks`, `railpack` |
| `apps[].dockerfile` | no | Dockerfile path (default: `Dockerfile`, for `buildType: dockerfile`) |
| `apps[].dockerContextPath` | no | Docker build context path (for `buildType: dockerfile`) |
| `apps[].dockerBuildStage` | no | Docker build target stage (for `buildType: dockerfile`) |
| `apps[].publishDirectory` | no | Publish directory (for `buildType: static`) |
| `apps[].isStaticSpa` | no | Single Page Application mode (for `buildType: static`) |
| `apps[].autoDeploy` | no | Enable auto-deploy on push (`true`/`false`) |
| `apps[].replicas` | no | Number of app replicas (integer, minimum 1) |
| `apps[].buildPath` | no | Build path for GitHub provider (default: `/`) |
| `apps[].triggerType` | no | GitHub trigger type: `push` (default) or `manual` |
| `apps[].watchPaths` | no | File paths to watch for auto-deploy triggers (list of strings) |
| `apps[].create_env_file` | no | Write env vars to a `.env` file in the container working directory (default: `false`) |
| `apps[].volumes` | no | List of volume mount objects for persistent storage |
| `apps[].ports` | no | List of port mapping objects for TCP/UDP port exposure |
| `apps[].schedules` | no | List of cron job objects that run commands inside the app container |
| `apps[].composeFile` | if compose | Compose file — inline YAML block scalar (`\|`) or relative file path (for `sourceType: raw`), or in-repo path sent as `composePath` to Dokploy (for `sourceType: github`) |
| `apps[].composeType` | no | Compose type: `docker-compose` (default) or `stack` |
| `apps[].sourceType` | no | Compose source type: `raw` (default — inline/local file) or `github` (server-side repo clone) |

### Volume Mount Object

| Key | Required | Description |
|-----|----------|-------------|
| `volume.source` | yes | Volume name (for `type: volume`) or host path (for `type: bind`) |
| `volume.target` | yes | Mount path inside the container |
| `volume.type` | yes | `volume` (Docker-managed) or `bind` (host path) |

### Port Object

| Key                | Required | Description                                   |
|--------------------|----------|-----------------------------------------------|
| `port.publishedPort` | yes      | Host port to publish                          |
| `port.targetPort`  | yes      | Container port to map to                      |
| `port.protocol`    | no       | `tcp` (default) or `udp`                      |
| `port.publishMode` | no       | `ingress` (default) or `host` (Swarm publish) |

On first `setup`, ports are created via the Dokploy `port.create` API. On subsequent `apply` (redeploy), ports are reconciled by `publishedPort`: existing ports are updated, new ones are created, and removed ones are deleted.

### Schedule Object

| Key | Required | Description |
|-----|----------|-------------|
| `schedule.name` | yes | Job name (used to match during reconciliation on redeploy) |
| `schedule.cronExpression` | yes | Standard 5-field cron: `minute hour day month weekday` |
| `schedule.command` | yes | Command to run inside the app container via `docker exec` |
| `schedule.shellType` | no | `bash` (default) or `sh` |
| `schedule.timezone` | no | IANA timezone (e.g. `America/Chicago`) |
| `schedule.enabled` | no | Whether the schedule is active (default: `true`) |

On first `setup`, schedules are created via the Dokploy `schedule.create` API. On subsequent `apply` (redeploy), schedules are reconciled by name: existing schedules are updated, new ones are created, and removed ones are deleted.

Run a schedule on demand (outside its cron expression) with:

```bash
ic --env prod run-schedule <app> <schedule-name>
```

`app` is optional and auto-selects when only one app is configured.

### Domain Object

| Key                      | Required   | Description                                                                       |
| ------------------------ | ---------- | --------------------------------------------------------------------------------- |
| `domain.host`            | yes        | Domain hostname                                                                   |
| `domain.port`            | yes        | Container port to expose                                                          |
| `domain.https`           | yes        | Enable HTTPS                                                                      |
| `domain.certificateType` | yes        | `none`, `letsencrypt`, or `custom`                                                |
| `domain.certificate`     | if custom  | Certificate name (from `certificates` section) when `certificateType` is `custom` |
| `domain.path`            | no         | URL path (default: `/`)                                                           |
| `domain.internalPath`    | no         | Internal routing path (default: `/`)                                              |
| `domain.stripPath`       | no         | Strip path prefix before forwarding (default: `false`)                            |
| `domain.serviceName`     | if compose | Target service name within a compose stack for Traefik routing                    |

## Certificates

Custom SSL/TLS certificates can be declared at the top level and referenced by domains. Certificate files are read at deploy time and uploaded to Dokploy.

```yaml
certificates:
  - name: wildcard-example
    certFile: certs/wildcard.pem
    keyFile: certs/wildcard.key
    autoRenew: false
```

| Key                        | Required | Description                                          |
| -------------------------- | -------- | ---------------------------------------------------- |
| `certificates[].name`      | yes      | Unique certificate name                              |
| `certificates[].certFile`  | yes      | Path to PEM certificate file (relative to repo root) |
| `certificates[].keyFile`   | yes      | Path to PEM private key file (relative to repo root) |
| `certificates[].autoRenew` | no       | Enable auto-renewal                                  |

Reference a certificate from a domain:

```yaml
domain:
  host: app.example.com
  port: 8000
  https: true
  certificateType: custom
  certificate: wildcard-example
```

On `apply` (redeploy), certificates removed from `dokploy.yml` are deleted from Dokploy — but only if icarus created them. Deletion is tracked against icarus's own state, not the full list of certificates on the server, so certificates managed outside of icarus (or by other tools) are left alone.

## Compose Apps

Apps with `source: compose` deploy a full Docker Compose stack as a single Dokploy resource.

### Raw source (default)

The compose file is stored inline in `dokploy.yml` or read from a local path at deploy time. Dokploy receives the full file content — suitable for stacks that only pull pre-built images.

```yaml
apps:
  # Inline compose file
  - name: my-stack
    source: compose
    composeFile: |
      services:
        web:
          image: nginx
          ports:
            - "80"
    composeType: docker-compose

  # External compose file (relative to dokploy.yml)
  - name: my-stack
    source: compose
    composeFile: docker-compose.yml
    composeType: docker-compose
```

### GitHub source

Set `sourceType: github` when the compose stack builds images from source (i.e. the compose file references `build:` directives). Dokploy clones the repository server-side, giving the build a real context with the Dockerfile and all source files present.

Requires a top-level `github:` block (same as for `source: github` applications). `composeFile` is the path to the compose file within the repository.

```yaml
github:
  owner: my-org
  repository: my-app
  branch: main

apps:
  - name: my-stack
    source: compose
    sourceType: github
    composeFile: docker-compose.yml
    composeType: docker-compose
```

This resolves the `failed to solve: open Dockerfile: no such file or directory` error that occurs when using `build:` directives with the default raw source.

### Env vars and domains

Compose env vars (defined in per-app `env:` or pushed from the project `.env` via `env_targets`) are available in the compose file using standard `${VAR}` syntax. Dokploy resolves these at deploy time. Use `$${VAR}` to escape literal `$` in shell contexts (e.g. healthcheck commands).

Domains for compose apps require `serviceName` to identify which service in the stack receives traffic:

```yaml
environments:
  prod:
    apps:
      my-stack:
        domain:
          host: app.example.com
          port: 8080
          https: true
          certificateType: letsencrypt
          serviceName: web
```

See `examples/docker-compose/` for a complete working example.

## `database`

Database resources managed by Dokploy. Created and deployed during `setup`, included in `status` output, and destroyed when the project is deleted via `destroy`.

Databases are **not** part of `deploy_order` — they are automatically deployed immediately after creation during setup.

| Key                               | Required                        | Description                                                      |
| --------------------------------- | ------------------------------- | ---------------------------------------------------------------- |
| `database[].name`                 | yes                             | Unique database name within the project                          |
| `database[].type`                 | yes                             | Engine type: `postgres`, `mysql`, `mariadb`, `mongo`, or `redis` |
| `database[].dockerImage`          | no                              | Docker image override (defaults per type)                        |
| `database[].databaseName`         | postgres, mysql, mariadb        | Database name to create                                          |
| `database[].databaseUser`         | postgres, mysql, mariadb, mongo | Database user                                                    |
| `database[].databasePassword`     | yes                             | Database password                                                |
| `database[].databaseRootPassword` | mysql, mariadb                  | Root password                                                    |
| `database[].description`          | no                              | Optional description                                             |

### Type-Specific Requirements

| Type     | Required Fields                                                            |
| -------- | -------------------------------------------------------------------------- |
| postgres | `databaseName`, `databaseUser`, `databasePassword`                         |
| mysql    | `databaseName`, `databaseUser`, `databasePassword`, `databaseRootPassword` |
| mariadb  | `databaseName`, `databaseUser`, `databasePassword`, `databaseRootPassword` |
| mongo    | `databaseUser`, `databasePassword`                                         |
| redis    | `databasePassword`                                                         |

### Example

```yaml
database:
  - name: app-db
    type: postgres
    databaseName: myapp
    databaseUser: myuser
    databasePassword: changeme

  - name: cache
    type: redis
    databasePassword: redis_secret
```

On subsequent `apply` (redeploy), `dockerImage` and `description` are diffed against the live database and updated via `<type>.update`. Changing `dockerImage` also triggers `<type>.rebuild` to restart the container on the new image — updating the field alone does not restart it.

## `destinations`

S3-compatible storage targets referenced by `backups` and `volumeBackups` entries.

| Key                              | Required | Description                                                                                    |
| -------------------------------- | -------- | ------------------------------------------------------------------------------------------------ |
| `destinations[].name`            | yes      | Unique destination name, referenced by `backups[].destination` / `volumeBackups[].destination` |
| `destinations[].accessKey`       | yes      | S3 access key ID                                                                                |
| `destinations[].secretAccessKey` | yes      | S3 secret access key                                                                            |
| `destinations[].bucket`          | yes      | S3 bucket name                                                                                  |
| `destinations[].region`          | yes      | S3 region (e.g. `us-east-1`)                                                                    |
| `destinations[].endpoint`        | yes      | S3-compatible endpoint URL                                                                      |

```yaml
destinations:
  - name: s3-backups
    accessKey: AKIAIOSFODNN7EXAMPLE
    secretAccessKey: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
    bucket: my-dokploy-backups
    region: us-east-1
    endpoint: https://s3.amazonaws.com
```

## `database[].backups`

Scheduled database backups, one per `database` entry. Postgres, MySQL, MariaDB, and Mongo support scheduled backups; Redis does not.

| Key                         | Required | Description                                        |
| --------------------------- | -------- | --------------------------------------------------- |
| `backups[].schedule`        | yes      | 5-field cron expression for backup frequency       |
| `backups[].prefix`          | yes      | Prefix for backup files in the destination bucket  |
| `backups[].destination`     | yes      | Destination name (from `destinations`)             |
| `backups[].enabled`         | no       | Whether the schedule is active (default: `true`)   |
| `backups[].keepLatestCount` | no       | Number of latest backups to retain                 |

```yaml
database:
  - name: app-db
    type: postgres
    databaseName: myapp
    databaseUser: myuser
    databasePassword: changeme
    backups:
      - schedule: "0 0 * * *"
        prefix: daily
        destination: s3-backups
        keepLatestCount: 7
```

Trigger a one-off backup outside the schedule with `ic backup <db-name>` (auto-selects the prefix if only one schedule exists, otherwise pass `--prefix`), or list existing backup files with `ic backup <db-name> --list`. See [api.md](api.md) for details.

## `volumeBackups`

Scheduled Docker volume backups, configurable on both `apps` entries and `database` entries. Unlike `backups`, which is Postgres/MySQL/MariaDB/Mongo-specific, `volumeBackups` works for any service (application, compose, or database) and backs up a named Docker volume directly.

| Key                               | Required | Description                                        |
| --------------------------------- | -------- | --------------------------------------------------- |
| `volumeBackups[].name`            | yes      | Unique name for this volume backup schedule        |
| `volumeBackups[].volumeName`      | yes      | Docker volume name to back up                      |
| `volumeBackups[].prefix`          | yes      | Prefix for backup files in the destination bucket  |
| `volumeBackups[].cronExpression`  | yes      | 5-field cron expression for backup frequency       |
| `volumeBackups[].destination`     | yes      | Destination name (from `destinations`)             |
| `volumeBackups[].enabled`         | no       | Whether the schedule is active (default: `true`)   |
| `volumeBackups[].keepLatestCount` | no       | Number of latest backups to retain                 |

```yaml
apps:
  - name: web
    source: docker
    dockerImage: nginx:alpine
    volumeBackups:
      - name: web-uploads
        volumeName: web_uploads
        prefix: uploads
        cronExpression: "0 4 * * *"
        destination: s3-backups

database:
  - name: app-db
    type: postgres
    databaseName: myapp
    databaseUser: myuser
    databasePassword: changeme
    volumeBackups:
      - name: pg-data
        volumeName: app-db_postgres_data
        prefix: pg-data
        cronExpression: "0 3 * * *"
        destination: s3-backups
```

Trigger a volume backup manually with `ic backup <resource> --volume <name>`, where `<resource>` is an app, compose, or database name. See [api.md](api.md) for details.

## `{app_name}` Resolution

In `command` and `env` fields, `{app_name}` placeholders are replaced with the Dokploy-assigned `appName` from the state file. This allows apps to reference each other's internal Docker network hostnames.

Example: `redis://{redis}:6379/0` resolves to `redis://redis-abcdef:6379/0` (where `redis-abcdef` is the Dokploy appName).

Resolution happens during `setup` (for commands) and `env` (for environment variables).

## Environment Variables

| Variable               | Required      | Default | Description                                                      |
| ---------------------- | ------------- | ------- | ---------------------------------------------------------------- |
| `DOKPLOY_URL`          | yes           | —       | Dokploy server URL                                               |
| `DOKPLOY_API_KEY`      | yes           | —       | API key for authentication                                       |
| `DOKPLOY_ENV`          | no            | `dev`   | Target environment (alternative to `--env` flag)                 |
| `DOTENV_FILE`          | no            | `.env`  | Path to `.env` file for config and push (see below)              |
| `ENV_EXCLUDES`         | no            | —       | Extra env var exclusion patterns when pushing `.env` (see below) |
| `ENV_EXCLUDE_PREFIXES` | no            | —       | Legacy alias for `ENV_EXCLUDES`                                  |
| `DOKPLOY_SSH_HOST`     | for logs/exec | —       | SSH host for Docker access (IP or hostname)                      |
| `DOKPLOY_SSH_USER`     | no            | `root`  | SSH user for Docker access                                       |
| `DOKPLOY_SSH_PORT`     | no            | `22`    | SSH port for Docker access                                       |

Resolution order: `--env` flag > `DOKPLOY_ENV` (from `.env` or environment) > `dev`.

The `DOKPLOY_SSH_*` variables are only required for the `logs` and `exec` commands, which connect to the Docker daemon on the Dokploy host via SSH.

### Env File Selection

By default, `ic env` reads `.env` from the repo root. You can override this with:

- **`--env-file <path>`** CLI flag (highest priority)
- **`DOTENV_FILE`** environment variable (follows the [python-decouple convention](https://github.com/HBNetwork/python-decouple#how-do-i-use-it-with-django))

```bash
ic --env prod --env-file .env.prod env     # explicit file path
DOTENV_FILE=.env.prod ic --env prod env    # via env var
```

### Process Environment Override

When pushing env vars, `ic env` resolves each value through python-decouple's `Config`, which checks `os.environ` before the `.env` file. This means process environment variables automatically override file values for matching keys:

```bash
doppler run -- ic --env prod env    # Doppler secrets override .env values
```

Only keys present in the `.env` file are pushed — stray process env vars (like `PATH` or `HOME`) are never included.

### Env Exclusion Patterns

`ENV_EXCLUDES` (and the legacy `ENV_EXCLUDE_PREFIXES`) accepts a comma-separated list of patterns that control which `.env` variables are excluded when pushing with `ic env`:

| Pattern    | Type         | Example match                        |
|------------|--------------|--------------------------------------|
| `DEV`      | Exact match  | Excludes `DEV` only                  |
| `COMPOSE_` | Prefix match | Excludes `COMPOSE_FILE`, `COMPOSE_X` |
| `SECRET*`  | Prefix match | Excludes `SECRET_KEY`, `SECRETABC`   |

Rules:

- Patterns ending with `_` or `*` are **prefix matches** (the `*` is stripped before comparison).
- All other patterns are **exact matches**.
- Both `ENV_EXCLUDES` and `ENV_EXCLUDE_PREFIXES` are read and merged with the built-in defaults.

Example: `ENV_EXCLUDES=DEV,DEBUG,MY_SECRET_` excludes the exact keys `DEV` and `DEBUG`, plus any key starting with `MY_SECRET_`.

## Schema Directive

The `# yaml-language-server` directive at the top of `dokploy.yml` enables IDE features:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/pythoninthegrass/icarus/main/schemas/dokploy.schema.json
```

This works in VS Code (with the YAML extension), JetBrains IDEs, and other editors that support the yaml-language-server protocol. You can also use a local relative path (`$schema=schemas/dokploy.schema.json`) if you have a copy of the schema in your repo.

## Host Tuning

Dokploy hosts running many containers (especially compose stacks) can exhaust default Linux inotify limits, causing `tail: inotify cannot be used, reverting to polling: Too many open files` during deployments.

Apply these sysctl settings on the Dokploy host:

```bash
echo "fs.inotify.max_user_instances=512" | sudo tee -a /etc/sysctl.conf
echo "fs.inotify.max_user_watches=524288" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

| Setting | Default | Recommended | Purpose |
|---------|---------|-------------|---------|
| `fs.inotify.max_user_instances` | 128 | 512 | Max inotify instances per user (each container watcher uses one) |
| `fs.inotify.max_user_watches` | ~250,000 | 524,288 | Max files watched across all instances |
