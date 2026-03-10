import contextlib
import httpx
import importlib.util
import os
import pytest
import uuid
import yaml
from hypothesis import HealthCheck, Phase, settings
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"
_spec = importlib.util.spec_from_file_location("dokploy", _SCRIPT)
_dokploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dokploy)

# Hypothesis profiles: "ci" for deterministic CI runs, "dev" for thorough local exploration.
settings.register_profile(
    "ci",
    max_examples=100,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "dev",
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return yaml.safe_load((FIXTURES_DIR / name).read_text())


@pytest.fixture
def web_app_config():
    """Realistic config based on examples/web-app/dokploy.yml."""
    return _load_fixture("web_app_config.yml")


@pytest.fixture
def minimal_config():
    """Minimal valid config based on examples/minimal/dokploy.yml."""
    return _load_fixture("minimal_config.yml")


@pytest.fixture
def docker_only_config():
    """Docker-only config based on examples/docker-only/dokploy.yml."""
    return _load_fixture("docker_only_config.yml")


@pytest.fixture
def sample_state():
    """Sample state dict as saved by cmd_setup."""
    return _load_fixture("sample_state.yml")


@pytest.fixture
def github_static_config():
    """GitHub static-site config with new fields (buildType, domain.path, etc.)."""
    return _load_fixture("github_static_config.yml")


@pytest.fixture
def github_dockerfile_config():
    """GitHub dockerfile config with new fields (watchPaths, autoDeploy, etc.)."""
    return _load_fixture("github_dockerfile_config.yml")


@pytest.fixture
def dokploy_yml(tmp_path, minimal_config):
    """Write a minimal dokploy.yml in tmp_path and return the path."""
    config_file = tmp_path / "dokploy.yml"
    config_file.write_text(yaml.dump(minimal_config))
    return config_file


@pytest.fixture(scope="session")
def dokploy_url():
    """Dokploy base URL from env var, default http://localhost:3000."""
    return os.environ.get("DOKPLOY_URL", "http://localhost:3000")


@pytest.fixture(scope="session")
def api_key():
    """Dokploy API key from env var."""
    return os.environ.get("DOKPLOY_API_KEY", "")


@pytest.fixture(autouse=True, scope="session")
def skip_if_no_dokploy(request, dokploy_url, api_key):
    """Skip all e2e tests when Dokploy is unreachable or no API key is set."""
    if not any(item.get_closest_marker("e2e") for item in request.session.items):
        return
    if not api_key:
        pytest.skip("DOKPLOY_API_KEY not set")
    try:
        httpx.get(f"{dokploy_url}/api/trpc/settings.health", timeout=5.0)
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip(f"Dokploy unreachable at {dokploy_url}")


@pytest.fixture(scope="session")
def e2e_client(dokploy_url, api_key):
    """Real DokployClient pointed at a live Dokploy instance."""
    return _dokploy.DokployClient(dokploy_url, api_key)


@pytest.fixture
def e2e_config():
    """Minimal Docker-only config with a uuid-suffixed project name."""
    suffix = uuid.uuid4().hex[:8]
    return {
        "project": {
            "name": f"e2e-{suffix}",
            "description": "E2E test project",
            "deploy_order": [["app"]],
        },
        "apps": [
            {
                "name": "app",
                "source": "docker",
                "dockerImage": "nginx:alpine",
            }
        ],
    }


@pytest.fixture
def e2e_project(e2e_client, e2e_config, tmp_path):
    """Run cmd_setup, yield (config, state_file, state), then cmd_destroy."""
    state_file = tmp_path / ".dokploy-state" / "e2e.json"
    _dokploy.cmd_setup(e2e_client, e2e_config, state_file)
    import json

    state = json.loads(state_file.read_text())
    try:
        yield e2e_config, state_file, state
    finally:
        with contextlib.suppress(Exception):
            _dokploy.cmd_destroy(e2e_client, state_file)
