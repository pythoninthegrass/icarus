import os

import pytest
import yaml
from hypothesis import HealthCheck, Phase, settings
from pathlib import Path

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
