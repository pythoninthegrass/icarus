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

- **`saveBuildType`**: `dockerfile`, `dockerContextPath`, and `dockerBuildStage` must be explicit strings (not `null`) — use `"Dockerfile"`, `""`, `""` respectively. Passing `null` causes Dokploy to use the clone directory name as the Dockerfile path.

- **`project.remove`** (not `project.delete`) is the correct endpoint for project deletion.

- **`application.saveBuildType`** is a separate endpoint from `application.update` — build type configuration cannot be set via the general update endpoint.

- **`application.deploy`** returns an empty response body on success.

- **`project.create`** returns a nested structure: `{"project": {...}, "environment": {...}}`.
