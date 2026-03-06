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
| `property` | Property-based tests (Hypothesis) |
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
  conftest.py              # Shared pytest fixtures + Hypothesis profiles
  strategies.py            # Reusable Hypothesis strategies
  test_unit.py             # Unit tests for pure functions in dokploy.py
  test_property.py         # Property-based tests (Hypothesis)
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

This happens at module level in `test_unit.py` and `test_property.py`. Functions under test are then accessed as `dokploy.function_name()`.

## Property-Based Tests (Hypothesis)

`tests/test_property.py` uses [Hypothesis](https://hypothesis.readthedocs.io/) to fuzz pure functions with generated inputs. Reusable strategies live in `tests/strategies.py`.

### Hypothesis Profiles

Configured in `conftest.py`, selected via `HYPOTHESIS_PROFILE` env var:

| Profile | `max_examples` | `derandomize` | `database` | Use case |
|---------|----------------|---------------|------------|----------|
| `default` | 100 | No | Yes | Local development (Hypothesis default) |
| `ci` | 100 | Yes | None | Deterministic CI runs |
| `dev` | 500 | No | Yes | Thorough local exploration |

```bash
HYPOTHESIS_PROFILE=ci uv run pytest tests/ -m property
HYPOTHESIS_PROFILE=dev uv run pytest tests/ -m property
```

### Strategies (`tests/strategies.py`)

| Strategy | Generates |
|----------|-----------|
| `app_name` | Lowercase names with hyphens (`[a-z][a-z0-9-]{0,15}`) |
| `ref_compatible_name` | `\w`-only names for `resolve_refs` tests (no hyphens) |
| `env_content()` | Realistic `.env` file content (KEY=value, comments, blanks) |
| `exclude_prefixes()` | Lists of uppercase prefixes |
| `state_dict()` | State dict with apps mapping; `ref_safe=True` for resolve_refs |
| `app_config()` | Valid app config dict |
| `dokploy_config()` | Full valid config matching the JSON schema |

### Properties Tested

| Function | Properties |
|----------|------------|
| `filter_env` | Idempotent, no excluded keys survive, no comments/blanks, output is subset of input |
| `resolve_refs` | Known refs resolved, unknown refs preserved, no-refs unchanged, mixed refs |
| `merge_env_overrides` | Original immutable, override wins, base preserved, missing env is identity |
| `validate_config` | Valid configs pass, invalid deploy_order/env_targets caught |
| `validate_env_references` | Valid refs pass, invalid env app refs caught |
| Pipeline | YAML round-trip stability, merge-then-validate, serialization-then-validate |
