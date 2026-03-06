# Contributing

Contributions are welcome. Please open an issue before starting work on large changes.

## Issues

- Use the [bug report](.github/ISSUE_TEMPLATE/bug_report.md) template for bugs
- Use the [feature request](.github/ISSUE_TEMPLATE/feature_request.md) template for enhancements

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Ensure `dokploy.py --help` still works: `uv run --script dokploy.py --help`
4. Ensure example configs validate against the schema
5. Ensure markdown passes linting: `markdownlint '**/*.md'`
6. Open a pull request using the [PR template](.github/PULL_REQUEST_TEMPLATE.md)

## Code Style

- Python: formatted with [ruff](https://docs.astral.sh/ruff/) (line length 88, 4-space indent)
- YAML/JSON: 2-space indent
- Markdown: validated with [markdownlint](https://github.com/markdownlint/markdownlint)

## Schema Updates

When modifying `dokploy.yml` structure:

1. Update `schemas/dokploy.schema.json` to match
2. Update `docs/configuration.md` reference table
3. Update `dokploy.yml.example` if the change affects the starter config
4. Verify example configs still validate
