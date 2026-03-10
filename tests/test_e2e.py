"""End-to-end tests against a real Dokploy instance."""

import importlib.util
import json
import pytest
from pathlib import Path

pytestmark = pytest.mark.e2e

_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"
_spec = importlib.util.spec_from_file_location("dokploy", _SCRIPT)
dokploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dokploy)


@pytest.mark.timeout(120)
class TestE2ELifecycle:
    def test_setup_creates_state_file(self, e2e_project):
        """cmd_setup writes a state file with projectId, environmentId, and apps."""
        _config, state_file, state = e2e_project
        assert state_file.exists()
        assert "projectId" in state
        assert "environmentId" in state
        assert "app" in state["apps"]
        assert "applicationId" in state["apps"]["app"]
        assert "appName" in state["apps"]["app"]

    def test_project_visible_after_setup(self, e2e_client, e2e_project):
        """The created project appears in project.all."""
        _config, _state_file, state = e2e_project
        projects = e2e_client.get("project.all")
        project_ids = [p["projectId"] for p in projects]
        assert state["projectId"] in project_ids

    def test_app_has_docker_image(self, e2e_client, e2e_project):
        """The created app has the correct dockerImage set."""
        _config, _state_file, state = e2e_project
        app_id = state["apps"]["app"]["applicationId"]
        app = e2e_client.get("application.one", params={"applicationId": app_id})
        assert app["dockerImage"] == "nginx:alpine"

    def test_full_lifecycle(self, e2e_client, e2e_config, tmp_path):
        """Full lifecycle: setup -> env -> deploy -> status -> destroy."""
        state_file = tmp_path / ".dokploy-state" / "e2e.json"

        # setup
        dokploy.cmd_setup(e2e_client, e2e_config, state_file)
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert "projectId" in state

        try:
            # env (no env_targets in config, so this is a no-op but should not error)
            dokploy.cmd_env(e2e_client, e2e_config, state_file, tmp_path)

            # deploy
            dokploy.cmd_deploy(e2e_client, e2e_config, state_file)

            # status
            dokploy.cmd_status(e2e_client, state_file)
        finally:
            # destroy
            dokploy.cmd_destroy(e2e_client, state_file)

        assert not state_file.exists()

    def test_destroy_removes_project(self, e2e_client, e2e_config, tmp_path):
        """After destroy, the project is absent from project.all."""
        state_file = tmp_path / ".dokploy-state" / "e2e.json"
        dokploy.cmd_setup(e2e_client, e2e_config, state_file)
        state = json.loads(state_file.read_text())
        project_id = state["projectId"]

        dokploy.cmd_destroy(e2e_client, state_file)

        projects = e2e_client.get("project.all")
        project_ids = [p["projectId"] for p in projects]
        assert project_id not in project_ids
