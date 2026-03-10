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
  conftest.py              # Shared pytest fixtures + Hypothesis profiles + E2E fixtures
  strategies.py            # Reusable Hypothesis strategies
  test_unit.py             # Unit tests for pure functions in main.py
  test_integration.py      # Integration tests for DokployClient and cmd_* functions
  test_property.py         # Property-based tests (Hypothesis)
  test_e2e.py              # E2E tests against a real Dokploy instance
  setup-dokploy.sh         # Reusable Dokploy bootstrap for E2E (Linux only)
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

## Importing `main.py`

`main.py` is a PEP 723 inline script, not a package. Tests import it using `importlib`:

```python
_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"
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

## E2E Tests

E2E tests run `main.py` commands against a real Dokploy instance. They are marked with `@pytest.mark.e2e` and **skipped by default** via `addopts = "-m 'not e2e'"` in `pyproject.toml`.

### Prerequisites

- Linux host with Docker installed (Docker Swarm is initialized by the setup script)
- Ports: 3000 (Dokploy), 5432 (Postgres), 6379 (Redis)
- `curl` and `jq` available on the host

### Local Setup (OrbStack on macOS)

Using Taskfile (recommended):

```bash
task e2e:snapshot                # build base VM snapshot (first time only)
task e2e:setup                   # import snapshot + run setup-dokploy.sh
task e2e:health                  # verify Dokploy is reachable
```

Or manually:

```bash
orb create ubuntu:noble dokploy-e2e -c tests/cloud-init.yml
orb -m dokploy-e2e -u root bash /mnt/mac$(pwd)/tests/setup-dokploy.sh
curl http://dokploy-e2e.orb.local:3000/api/trpc/settings.health
```

### Cloud-init Networking (OrbStack)

OrbStack VMs use `systemd-resolved` with a stub listener at `127.0.0.53`.
The Docker daemon resolves DNS via `/etc/resolv.conf` (which symlinks to the
stub), not through NSS like userspace tools. If `systemd-resolved` isn't
ready when Docker pulls images, pulls fail with "connection refused" on
`127.0.0.53:53`.

`tests/cloud-init.yml` works around this with three fixes:

1. **Netplan nameservers** -- `/etc/netplan/50-cloud-init.yaml` adds
   Cloudflare DNS (`1.1.1.1`, `1.0.0.1`) so `systemd-resolved` has
   upstream servers configured immediately.

2. **Static `/etc/resolv.conf`** -- `runcmd` breaks the symlink to
   the stub resolver and writes real nameservers directly. This is
   what the Docker daemon reads for its own operations (image pulls,
   registry auth).

3. **Docker `daemon.json`** -- `/etc/docker/daemon.json` sets
   `"dns": ["1.1.1.1", "1.0.0.1"]` for container DNS resolution.
   This does not affect the daemon itself (only containers), but
   ensures services started via `docker service create` can resolve
   external hosts.

`setup-dokploy.sh` also includes a `getent hosts` wait loop before pulling
as belt-and-suspenders, but the static `resolv.conf` is the primary fix.

### Running E2E Tests

Using Taskfile:

```bash
task e2e                         # run against existing Dokploy instance
task e2e:run                     # one-shot: snapshot + setup + test + teardown
task e2e:run --force             # rebuild snapshot and run
```

Or manually with pytest:

```bash
DOKPLOY_URL=http://dokploy-e2e.orb.local:3000 \
DOKPLOY_API_KEY=<key from setup script> \
uv run pytest tests/ -m e2e -o "addopts=" -v --timeout=120
```

On a native Linux host where Dokploy is running locally:

```bash
DOKPLOY_URL=http://localhost:3000 \
DOKPLOY_API_KEY=<key> \
uv run pytest tests/ -m e2e -o "addopts=" -v --timeout=120
```

### CI (GitHub Actions)

The `.github/workflows/e2e.yml` workflow:

1. Runs on `ubuntu-latest` (native Docker, no DinD)
2. Executes `tests/setup-dokploy.sh` to bootstrap Dokploy
3. Installs Python 3.13 via `astral-sh/setup-uv`
4. Runs `pytest -m e2e` with 120s timeout per test

The setup script writes `DOKPLOY_URL` and `DOKPLOY_API_KEY` to `$GITHUB_OUTPUT` for subsequent steps.

### E2E Fixtures

Defined in `tests/conftest.py`:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `dokploy_url` | session | `DOKPLOY_URL` env var, default `http://localhost:3000` |
| `api_key` | session | `DOKPLOY_API_KEY` env var |
| `skip_if_no_dokploy` | session (autouse) | Skips e2e tests if Dokploy unreachable or no API key |
| `e2e_client` | session | Real `DokployClient` instance |
| `e2e_config` | function | Minimal Docker-only config with `e2e-{uuid}` project name |
| `e2e_project` | function | Calls `cmd_setup`, yields context, calls `cmd_destroy` in finalizer |

### VM Snapshots

`task e2e:snapshot` builds a base VM image (post-cloud-init + swarm init + image pulls, pre-services) and exports it to `.cache/dokploy-e2e.tar.zst`. Task fingerprints `tests/cloud-init.yml` and `tests/setup-dokploy.sh` -- the snapshot is only rebuilt when these files change.

`task e2e:run` depends on `e2e:snapshot`, so the first run builds the snapshot automatically. Subsequent runs import the cached image and only run `setup-dokploy.sh` (services + admin setup), skipping cloud-init and image pulls entirely.

`setup-dokploy.sh` supports a `PREPARE_ONLY=1` env var that stops after image pulls. The snapshot task uses this to capture the VM at the right point.

To force a rebuild: `task e2e:snapshot --force`.

### Design Decisions

- **No DinD**: OrbStack VMs and GHA runners have native Docker; the Dokploy install script blocks inside containers
- **No Traefik**: Tests hit port 3000 directly; only postgres + redis + dokploy services are needed
- **UUID project names**: `e2e-{uuid[:8]}` prevents collisions between parallel runs
- **Fixture-based cleanup**: yield fixture with try/except finalizer ensures `cmd_destroy` runs on failure
- **Base snapshot, not full**: snapshot captures cloud-init + Docker images but not running services. This keeps the `.tar.zst` small and avoids stale swarm state on import
