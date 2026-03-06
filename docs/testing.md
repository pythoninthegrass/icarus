# Testing

## Running Tests

```bash
uv run pytest tests/ -v              # all tests, verbose
uv run pytest tests/ -x              # stop on first failure
uv run pytest tests/ -k "filter_env"  # run tests matching keyword
uv run pytest tests/ --co            # list collected tests (dry run)
```

### Coverage

```bash
uv run pytest tests/ --cov=dokploy --cov-report=term-missing
```

### Parallel execution

```bash
uv run pytest tests/ -n auto         # requires pytest-xdist
```

## Test Dependencies

Defined in `pyproject.toml` under `[project.optional-dependencies] test`:

```bash
uv sync --extra test                 # install test deps into .venv
```

## Markers

Configured in `pyproject.toml` under `[tool.pytest.ini_options]`:

| Marker | Purpose |
|--------|---------|
| `unit` | Pure function tests (no network, no state) |
| `integration` | Tests that hit Dokploy APIs |
| `e2e` | Full workflow tests (setup -> deploy -> destroy) |
| `benchmark` | Performance benchmark tests |

Run a single marker:

```bash
uv run pytest tests/ -m unit
```

## Project Structure

```text
tests/
  conftest.py              # Shared pytest fixtures
  test_unit.py             # Unit tests for pure functions in dokploy.py
  fixtures/
    web_app_config.yml      # Realistic multi-app config (web, worker, redis)
    minimal_config.yml      # Single-app config
    docker_only_config.yml  # Docker-only stack (postgres, redis, app)
    sample_state.yml       # State dict as saved by cmd_setup
```

## Fixture Architecture

### YAML-backed fixtures

Test fixtures live in `tests/fixtures/*.yml` as plain YAML data files. `conftest.py` loads them via `yaml.safe_load()`:

```python
FIXTURES_DIR = Path(__file__).parent / "fixtures"

def _load_fixture(name: str) -> dict:
    return yaml.safe_load((FIXTURES_DIR / name).read_text())
```

Each fixture function calls `_load_fixture()`, which returns a **fresh dict per call**. This means test-level mutations (e.g. `minimal_config["project"]["env_targets"] = [...]`) are isolated between tests without needing `copy.deepcopy`.

### Why not use `examples/*.yml`?

The fixtures are simplified versions of the example configs (shorter commands, fewer env vars, different descriptions). Coupling tests to the examples would break tests when examples are updated for documentation reasons. Dedicated fixture files keep the test data stable.

### Available fixtures

| Fixture | File | Description |
|---------|------|-------------|
| `web_app_config` | `web_app_config.yml` | Multi-app config with GitHub apps, Docker apps, domains, two environments |
| `minimal_config` | `minimal_config.yml` | Single Docker app, one environment |
| `docker_only_config` | `docker_only_config.yml` | Docker-only stack with inter-app env refs |
| `sample_state` | `sample_state.yml` | State dict with `projectId`, `environmentId`, and app IDs |
| `dokploy_yml` | *(generated)* | Writes `minimal_config` to a temp `dokploy.yml` file, returns the path |

### Adding a new fixture

1. Create `tests/fixtures/your_fixture.yml` with the data
2. Add a fixture function in `tests/conftest.py`:

```python
@pytest.fixture
def your_fixture():
    """Description of this fixture."""
    return _load_fixture("your_fixture.yml")
```

## Importing `dokploy.py`

`dokploy.py` is a PEP 723 inline script, not a package. Tests import it using `importlib`:

```python
_SCRIPT = Path(__file__).resolve().parent.parent / "dokploy.py"
_spec = importlib.util.spec_from_file_location("dokploy", _SCRIPT)
dokploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dokploy)
```

This happens at module level in `test_unit.py`. Functions under test are then accessed as `dokploy.function_name()`.
