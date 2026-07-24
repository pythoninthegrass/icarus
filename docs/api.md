# Dokploy API Notes

Quirks and gotchas discovered while building the deployment script.

## OpenAPI Schema

The Dokploy OpenAPI schema can be downloaded from the authenticated endpoint:

```text
GET /api/settings.getOpenApiDocument
```

Requires the `x-api-key` header.

## API Quirks

- **`saveGithubProvider`**: `repository` is the repo name only (e.g. `my-repo`), not `owner/repo` — Dokploy prepends the `owner` automatically.

- **`saveBuildType`**: `dockerfile`, `dockerContextPath`, and `dockerBuildStage` must be explicit strings (not `null`) — use `"Dockerfile"`, `""`, `""` respectively. Passing `null` causes Dokploy to use the clone directory name as the Dockerfile path. As of v0.28.4, `herokuVersion` and `railpackVersion` are also required (send `null` when not applicable). The OpenAPI spec marks them as nullable but the Zod validator rejects missing fields. The `buildType` enum values are: `dockerfile`, `nixpacks`, `static`, `heroku_buildpacks`, `paketo_buildpacks`, `railpack`.

- **`github.getGithubRepositories`**: GET with `?githubId=<id>`. Returns list of GitHub
  repo objects. Each repo has `owner.login` — used to auto-select the correct provider
  when multiple GitHub providers are configured.

- **`project.remove`** (not `project.delete`) is the correct endpoint for project deletion.

- **`application.saveBuildType`** is a separate endpoint from `application.update` — build type configuration cannot be set via the general update endpoint.

- **`application.saveEnvironment`**: `createEnvFile` (boolean) became a required field in Dokploy v0.28.4 (added in v0.26.1 as optional, promoted to required later). Controls whether env vars are written to a `.env` file in the container's working directory. Set `false` to preserve pre-v0.26.1 behavior (env vars injected as process environment only).

- **`mounts.create`**: Creates a persistent mount for an application. Required fields: `type` (`volume` or `bind`), `mountPath` (container path), `serviceId` (the application ID). Optional: `serviceType` (defaults to `application`). For `type: volume`, send `volumeName`; for `type: bind`, send `hostPath`. Note: the schema uses `additionalProperties: false` — do not send `applicationId`.

- **`application.deploy`** triggers a fresh build and deploy. Returns an empty response body on success. Since v0.26.2, deployments execute asynchronously in the background — the endpoint returns immediately.

- **`application.redeploy`**: Same as `application.deploy` but used for existing applications. Reuses the existing configuration and triggers a rebuild. Takes `{"applicationId": "..."}`. Used on the redeploy path when a state file already exists. **Caveat**: `application.redeploy` does not propagate env vars from the Dokploy database into the running Docker swarm service spec — it only rebuilds from the stored image. To ensure the live service has current env, `ic trigger` (the redeploy path) calls `docker service update --env-add` over SSH before triggering the rebuild. See [Docker service env sync](#docker-service-env-sync) below.

- **`schedule.create`**: Creates a cron job attached to an application. Required fields: `name`, `cronExpression` (5-field cron), `command`. Also requires `scheduleType` (`application`) and `applicationId`. Optional: `shellType` (`bash` or `sh`, default `bash`), `timezone` (IANA string), `enabled` (boolean, default `true`). Returns the full schedule object including `scheduleId`.

- **`schedule.list`**: GET with `?id=<applicationId>&scheduleType=application`. Returns an array of schedule objects for the given application.

- **`schedule.update`**: Same fields as create but uses `scheduleId` instead of `applicationId`/`scheduleType`. Only changed fields need to be sent.

- **`schedule.delete`**: POST with `{"scheduleId": "..."}`. Returns `true` on success.

- **`project.create`** returns a nested structure: `{"project": {...}, "environment": {...}}`.

- **`schedule.runManually`**: POST with `{"scheduleId": "..."}`. Triggers an on-demand run of a configured schedule outside its cron expression. Exposed via `ic run-schedule <app> <name>`.

- **`<type>.update`** (databases): Diffed against `<type>.one` on every redeploy for `dockerImage` and `description` drift. Only changed fields are sent, plus the id field (e.g. `postgresId`).

- **`<type>.rebuild`**: Required after a database `dockerImage` change — `<type>.update` alone does not restart the container with the new image. Called automatically after `<type>.update` whenever `dockerImage` changed.

- **`registry.testRegistry`** / **`destination.testConnection`**: Called immediately after `registry.create`/`.update` and `destination.create`/`.update` respectively, reusing the same create payload. A failed connection test (`httpx.HTTPStatusError`) aborts the reconcile with a `SystemExit` — better to fail fast during `apply` than discover bad credentials mid-deploy.

- **`certificates.remove`**: POST with `{"certificateId": "..."}`. `reconcile_certificates` now deletes certificates that were previously created by icarus (tracked in state) but removed from `dokploy.yml`. Deletion is scoped to icarus-tracked certificates only — `certificates.all` returns every certificate in the organization, not just icarus's, so diffing is done against `state["certificates"]`, not the full remote list.

- **`backup.manualBackupPostgres`/`MySql`/`Mariadb`/`Mongo`**: POST with `{"backupId": "..."}`. Triggers an on-demand run of an existing scheduled database backup outside its cron expression. There is no manual-backup endpoint for Redis. Exposed via `ic backup <db-name>`.

- **`backup.listBackupFiles`**: GET with `?destinationId=<id>&search=<prefix>`. Lists backup files already stored at a destination matching the given prefix. Exposed via `ic backup <db-name> --list`.

- **`backup.remove`** (not `backup.delete`) is the correct endpoint for deleting a database backup schedule. Takes `{"backupId": "..."}`.

- **`volumeBackups.create`/`.update`**: Requires `name`, `volumeName`, `prefix`, `cronExpression`, `destinationId`, `serviceType` (`application`, `compose`, or a database type), and the matching service id field (`applicationId`, `composeId`, `postgresId`, `mysqlId`, `mariadbId`, `mongoId`, `redisId`). Optional: `keepLatestCount`, `enabled`. Unlike database `backups`, `volumeBackups` back up a named Docker volume directly and work for any service type, including Redis and compose apps.

- **`volumeBackups.list`**: GET with `?id=<serviceId>&volumeBackupType=<serviceType>`. Returns the volume backup schedules for a given app/compose/database.

- **`volumeBackups.delete`/`.runManually`**: POST with `{"volumeBackupId": "..."}`. `runManually` triggers an on-demand run outside the cron schedule, exposed via `ic backup <resource> --volume <name>`.

## Non-goals

icarus is a declarative deploy tool (`dokploy.yml` + reconcile), not a 1:1 wrapper around the Dokploy API. The official `@dokploy/cli` covers all 449 endpoints across 45 groups; icarus deliberately does not wrap the panel-admin and imperative groups below. This boundary is intentional, not a coverage gap.

| Group                                                   | Endpoints        | Why out of scope                                                       |
| ------------------------------------------------------- | ---------------- | ---------------------------------------------------------------------- |
| notification                                             | 38               | Panel admin. Future candidate: declarative notify-on-deploy.           |
| settings                                                | 49               | Server/Traefik admin; `clean` already covers the prune subset via SSH.  |
| user, organization, sso, stripe, admin                  | 18, 10, 10, 7, 1 | Account/billing/tenant admin - not deployment.                         |
| ai                                                      | 9                | Dokploy AI features; unrelated to config-driven deploy.                 |
| cluster, swarm                                          | 4, 3             | Node/infra management.                                                 |
| patch                                                   | 12               | Server-side file patching; niche.                                       |
| docker                                                  | 7                | Redundant - icarus reads containers directly over SSH (`ssh.py`).      |
| preview-deployment, rollback                            | 4, 2             | Imperative deploy verbs - deferred with lifecycle verbs.               |
| lifecycle verbs (start/stop/reload/rebuild/cancel/kill) | n/a              | Imperative; icarus stays declarative this round.                       |

Notification and lifecycle verbs are noted above as future candidates should icarus's scope expand; the rest are panel-admin or redundant with icarus's existing SSH-based approach and are not expected to be revisited.

## Known Server-Side Issues

- **Stale Traefik configs after project destroy**: `project.remove` deletes the
  project and its applications from the Dokploy database but does not remove the
  corresponding Traefik dynamic config files from `/etc/dokploy/traefik/dynamic/`.
  Repeated destroy/recreate cycles accumulate orphaned `<appName>.yml` files, all
  with identical routing rules. Traefik round-robins across dead services, causing
  502 errors. **Workaround**: manually delete stale `.yml` files from the dynamic
  config directory. Traefik watches the directory and picks up removals without a
  restart.

## Container & Log Access

The Dokploy REST API does **not** expose a container logs endpoint. The UI uses WebSocket/tRPC subscriptions for real-time log streaming, which is not available via the REST API.

Instead, the `logs` and `exec` commands use `docker-py` with SSH transport (`ssh://user@host`) to connect to the Docker daemon on the Dokploy host directly. Container IDs are resolved via the REST API, then docker-py fetches logs or runs exec against those containers.

### Endpoints Used

- **`docker.getContainersByAppNameMatch`**: GET with `?appName=<appName>`. Returns a list of containers (running + exited) matching the Dokploy-assigned appName. Each entry has `containerId`, `name`, and `state` (`running`, `exited`, `created`).

- **`docker.getContainersByAppLabel`**: GET with `?appName=<appName>&type=standalone|swarm`. Similar to above but filters by deployment type label.

- **`docker.getServiceContainersByAppName`**: GET with `?appName=<appName>&serviceName=<service>`. Returns containers for a specific service within a compose/stack app.

### SSH Transport

`docker-py` with `use_ssh_client=True` spawns `ssh -- <host> docker system dial-stdio` as a subprocess, piping the Docker API through the user's local SSH binary. This uses existing SSH config, keys, and known_hosts.

## Docker Service Env Sync

`application.redeploy` restarts the existing swarm service image but does not
re-apply env vars saved to Dokploy's DB via `application.saveEnvironment`.
Running `ic env` followed by `ic trigger` would therefore leave containers with
stale env.

`ic trigger` (redeploy path only) reconciles this before triggering the rebuild:

1. Resolves the same per-app env strings that `ic env` would push (via
   `resolve_app_envs`).
2. Opens a single SSH connection to the Dokploy host and calls
   `docker service update --env-add KEY=VALUE … <appName>` for each application
   app (compose apps re-read env on `compose.redeploy` and are skipped).

`--env-add` is incremental — it only updates the specified keys and preserves
all other service spec fields (mounts, networks, resources, labels).  The
service restarts only if the effective env actually changed.

If `DOKPLOY_SSH_HOST` is not set, the sync step is skipped with a warning and
the redeploy proceeds normally.

## Health Check / Pre-flight

The `check` command uses `GET /api/project.all` to validate the API key.
This endpoint is a good choice for pre-flight auth validation because:

- It requires authentication (returns 401/403 with an invalid key)
- It returns 200 with a small JSON payload on success
- It has no side effects (read-only)
