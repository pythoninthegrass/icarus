"""Integration tests for DokployClient and cmd_* functions."""

import httpx
import importlib.util
import json
import pytest
import respx
import yaml
from pathlib import Path
from unittest.mock import patch

# Import main.py as a module despite it being a PEP 723 script.
_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"
_spec = importlib.util.spec_from_file_location("dokploy", _SCRIPT)
dokploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dokploy)

pytestmark = pytest.mark.integration

BASE_URL = "https://dokploy.test"
API_KEY = "test-api-key-123"


def _make_client(router: respx.Router) -> dokploy.DokployClient:
    """Create a DokployClient with a mocked transport."""
    client = dokploy.DokployClient(BASE_URL, API_KEY)
    mock_transport = httpx.MockTransport(router.handler)
    client.client = httpx.Client(
        transport=mock_transport,
        base_url=BASE_URL,
        headers={"x-api-key": API_KEY},
        timeout=60.0,
    )
    return client


# ---------------------------------------------------------------------------
# DokployClient tests
# ---------------------------------------------------------------------------


class TestDokployClientInit:
    def test_base_url_trailing_slash_stripped(self):
        client = dokploy.DokployClient("https://example.com/", API_KEY)
        assert str(client.client.base_url) == "https://example.com"

    def test_headers_set(self):
        client = dokploy.DokployClient(BASE_URL, API_KEY)
        assert client.client.headers["x-api-key"] == API_KEY

    def test_timeout_configured(self):
        client = dokploy.DokployClient(BASE_URL, API_KEY)
        assert client.client.timeout.connect == 60.0


class TestDokployClientGet:
    def test_url_construction_and_params(self):
        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json={"projects": []}))
        client = _make_client(router)
        result = client.get("project.all")
        assert result == {"projects": []}

    def test_params_forwarded(self):
        router = respx.Router()
        route = router.get(f"{BASE_URL}/api/application.one").mock(
            return_value=httpx.Response(200, json={"applicationId": "app-1", "applicationStatus": "running"})
        )
        client = _make_client(router)
        result = client.get("application.one", params={"applicationId": "app-1"})
        assert result["applicationId"] == "app-1"
        assert route.called

    def test_raises_on_4xx(self):
        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
        client = _make_client(router)
        with pytest.raises(httpx.HTTPStatusError):
            client.get("project.all")

    def test_raises_on_5xx(self):
        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(500, text="Internal Server Error"))
        client = _make_client(router)
        with pytest.raises(httpx.HTTPStatusError):
            client.get("project.all")


class TestDokployClientPost:
    def test_url_construction_and_json_body(self):
        router = respx.Router()
        route = router.post(f"{BASE_URL}/api/project.create").mock(
            return_value=httpx.Response(
                200,
                json={
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                },
            )
        )
        client = _make_client(router)
        result = client.post("project.create", {"name": "test", "description": "desc"})
        assert result["project"]["projectId"] == "proj-1"
        # Verify the JSON body was sent
        sent = json.loads(route.calls[0].request.content)
        assert sent == {"name": "test", "description": "desc"}

    def test_empty_response_returns_empty_dict(self):
        router = respx.Router()
        router.post(f"{BASE_URL}/api/application.deploy").mock(return_value=httpx.Response(200, content=b""))
        client = _make_client(router)
        result = client.post("application.deploy", {"applicationId": "app-1"})
        assert result == {}

    def test_no_payload_sends_empty_dict(self):
        router = respx.Router()
        route = router.post(f"{BASE_URL}/api/application.deploy").mock(return_value=httpx.Response(200, content=b""))
        client = _make_client(router)
        client.post("application.deploy")
        sent = json.loads(route.calls[0].request.content)
        assert sent == {}

    def test_raises_on_4xx(self):
        router = respx.Router()
        router.post(f"{BASE_URL}/api/project.create").mock(return_value=httpx.Response(400, json={"error": "bad request"}))
        client = _make_client(router)
        with pytest.raises(httpx.HTTPStatusError):
            client.post("project.create", {"name": ""})


# ---------------------------------------------------------------------------
# cmd_check tests
# ---------------------------------------------------------------------------


class TestCmdCheck:
    def test_all_pass(self, tmp_path, monkeypatch, capsys):
        """All checks pass with valid env vars, reachable server, valid API key, config."""
        monkeypatch.setenv("DOKPLOY_API_KEY", API_KEY)
        monkeypatch.setenv("DOKPLOY_URL", BASE_URL)

        # Write a valid config file
        config = {"project": {"name": "test"}, "apps": []}
        (tmp_path / "dokploy.yml").write_text(yaml.dump(config))

        with respx.mock:
            # Server reachability
            respx.get(BASE_URL).mock(return_value=httpx.Response(200))
            # API key check
            respx.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=[]))
            dokploy.cmd_check(tmp_path)

        output = capsys.readouterr().out
        assert "FAIL" not in output
        assert "PASS" in output

    def test_missing_api_key(self, tmp_path, monkeypatch, capsys):
        """Fails when DOKPLOY_API_KEY is not set."""
        from decouple import UndefinedValueError

        def _missing_config(key, **kwargs):
            if "default" in kwargs:
                return kwargs["default"]
            raise UndefinedValueError(f"{key} not found")

        monkeypatch.setattr(dokploy, "config", _missing_config)

        cfg = {"project": {"name": "test"}, "apps": []}
        (tmp_path / "dokploy.yml").write_text(yaml.dump(cfg))

        with pytest.raises(SystemExit):
            dokploy.cmd_check(tmp_path)

        output = capsys.readouterr().out
        assert "FAIL" in output
        assert "DOKPLOY_API_KEY" in output

    def test_unreachable_server(self, tmp_path, monkeypatch, capsys):
        """Fails when server is unreachable."""
        monkeypatch.setenv("DOKPLOY_API_KEY", API_KEY)
        monkeypatch.setenv("DOKPLOY_URL", BASE_URL)

        config = {"project": {"name": "test"}, "apps": []}
        (tmp_path / "dokploy.yml").write_text(yaml.dump(config))

        with respx.mock:
            respx.get(BASE_URL).mock(side_effect=httpx.ConnectError("refused"))
            respx.get(f"{BASE_URL}/api/project.all").mock(side_effect=httpx.ConnectError("refused"))

            with pytest.raises(SystemExit):
                dokploy.cmd_check(tmp_path)

        output = capsys.readouterr().out
        assert "FAIL" in output

    def test_invalid_api_key(self, tmp_path, monkeypatch, capsys):
        """Fails when API key returns non-200."""
        monkeypatch.setenv("DOKPLOY_API_KEY", "bad-key")
        monkeypatch.setenv("DOKPLOY_URL", BASE_URL)

        config = {"project": {"name": "test"}, "apps": []}
        (tmp_path / "dokploy.yml").write_text(yaml.dump(config))

        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
            mock.get(BASE_URL).mock(return_value=httpx.Response(200))

            with pytest.raises(SystemExit):
                dokploy.cmd_check(tmp_path)

        output = capsys.readouterr().out
        assert "FAIL" in output
        assert "API key invalid" in output

    def test_missing_config_file(self, tmp_path, monkeypatch, capsys):
        """Fails when dokploy.yml does not exist."""
        monkeypatch.setenv("DOKPLOY_API_KEY", API_KEY)
        monkeypatch.setenv("DOKPLOY_URL", BASE_URL)

        with respx.mock:
            respx.get(BASE_URL).mock(return_value=httpx.Response(200))
            respx.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=[]))

            with pytest.raises(SystemExit):
                dokploy.cmd_check(tmp_path)

        output = capsys.readouterr().out
        assert "FAIL" in output
        assert "dokploy.yml" in output


# ---------------------------------------------------------------------------
# cmd_setup tests
# ---------------------------------------------------------------------------


def _setup_router(
    router: respx.Router,
    *,
    with_github: bool = False,
    github_owner: str = "",
    app_names: list[str] | None = None,
) -> dict:
    """Wire up standard mock routes for cmd_setup and return expected IDs."""
    if app_names is None:
        app_names = ["app"]

    ids = {
        "projectId": "proj-test-001",
        "environmentId": "env-test-001",
        "githubId": "gh-provider-001",
        "apps": {},
    }

    # project.create
    router.post(f"{BASE_URL}/api/project.create").mock(
        return_value=httpx.Response(
            200,
            json={
                "project": {"projectId": ids["projectId"]},
                "environment": {"environmentId": ids["environmentId"]},
            },
        )
    )

    # github.githubProviders + getGithubRepositories
    if with_github:
        router.get(f"{BASE_URL}/api/github.githubProviders").mock(
            return_value=httpx.Response(200, json=[{"githubId": ids["githubId"]}])
        )
        router.get(f"{BASE_URL}/api/github.getGithubRepositories").mock(
            return_value=httpx.Response(
                200,
                json=[{"owner": {"login": github_owner}, "name": "test-repo"}],
            )
        )

    # application.create — side_effect to return different IDs per app
    app_counter = {"n": 0}

    def _create_app(request):
        body = json.loads(request.content)
        name = body["name"]
        app_counter["n"] += 1
        app_id = f"app-id-{app_counter['n']:03d}"
        app_name = f"{name}-generated"
        ids["apps"][name] = {"applicationId": app_id, "appName": app_name}
        return httpx.Response(200, json={"applicationId": app_id, "appName": app_name})

    router.post(f"{BASE_URL}/api/application.create").mock(side_effect=_create_app)

    # Catch-all for provider, build type, update, domain calls
    for endpoint in (
        "application.saveDockerProvider",
        "application.saveGithubProvider",
        "application.saveBuildType",
        "application.update",
        "domain.create",
    ):
        router.post(f"{BASE_URL}/api/{endpoint}").mock(return_value=httpx.Response(200, json={}))

    return ids


class TestCmdSetup:
    def test_happy_path_docker(self, tmp_path, minimal_config):
        """Setup with a Docker-only config creates project, app, and state file."""
        router = respx.Router()
        ids = _setup_router(router, app_names=["app"])
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, minimal_config, state_file)

        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["projectId"] == ids["projectId"]
        assert state["environmentId"] == ids["environmentId"]
        assert "app" in state["apps"]

    def test_happy_path_github(self, tmp_path, web_app_config):
        """Setup with GitHub-sourced apps calls saveGithubProvider and saveBuildType."""
        router = respx.Router()
        ids = _setup_router(
            router,
            with_github=True,
            github_owner="your-org",
            app_names=["redis", "web", "worker", "beat", "flower"],
        )
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, web_app_config, state_file)

        state = json.loads(state_file.read_text())
        assert len(state["apps"]) == 5
        for name in ("redis", "web", "worker", "beat", "flower"):
            assert name in state["apps"]

    def test_state_file_already_exists(self, tmp_path, minimal_config):
        """Exits with error when state file already exists."""
        router = respx.Router()
        _setup_router(router)
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("{}")

        with pytest.raises(SystemExit):
            dokploy.cmd_setup(client, minimal_config, state_file)

    def test_api_call_payloads(self, tmp_path, minimal_config):
        """Verify project.create payload matches config."""
        router = respx.Router()
        project_route = router.post(f"{BASE_URL}/api/project.create").mock(
            return_value=httpx.Response(
                200,
                json={
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                },
            )
        )
        router.post(f"{BASE_URL}/api/application.create").mock(
            return_value=httpx.Response(200, json={"applicationId": "app-1", "appName": "app-gen"})
        )
        router.post(f"{BASE_URL}/api/application.saveDockerProvider").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/application.update").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/application.saveBuildType").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/domain.create").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, minimal_config, state_file)

        # Verify project.create was called with correct payload
        create_payload = json.loads(project_route.calls[0].request.content)
        assert create_payload["name"] == "my-app"
        assert "description" in create_payload

    def test_docker_provider_payload(self, tmp_path, minimal_config):
        """Docker provider saves correct dockerImage."""
        router = respx.Router()
        router.post(f"{BASE_URL}/api/project.create").mock(
            return_value=httpx.Response(
                200,
                json={
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                },
            )
        )
        router.post(f"{BASE_URL}/api/application.create").mock(
            return_value=httpx.Response(200, json={"applicationId": "app-1", "appName": "app-gen"})
        )
        docker_route = router.post(f"{BASE_URL}/api/application.saveDockerProvider").mock(
            return_value=httpx.Response(200, json={})
        )
        router.post(f"{BASE_URL}/api/application.update").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/application.saveBuildType").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/domain.create").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, minimal_config, state_file)

        payload = json.loads(docker_route.calls[0].request.content)
        assert payload["dockerImage"] == "nginx:alpine"
        assert payload["applicationId"] == "app-1"

    def test_github_provider_payload_uses_repo_name_only(self, tmp_path, web_app_config):
        """saveGithubProvider uses repo name (not owner/repo) per API quirk."""
        router = respx.Router()
        router.post(f"{BASE_URL}/api/project.create").mock(
            return_value=httpx.Response(
                200,
                json={
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                },
            )
        )
        router.get(f"{BASE_URL}/api/github.githubProviders").mock(return_value=httpx.Response(200, json=[{"githubId": "gh-1"}]))
        router.get(f"{BASE_URL}/api/github.getGithubRepositories").mock(
            return_value=httpx.Response(
                200,
                json=[{"owner": {"login": "your-org"}, "name": "your-app"}],
            )
        )

        app_n = {"n": 0}

        def _create(request):
            app_n["n"] += 1
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "applicationId": f"app-{app_n['n']}",
                    "appName": f"{body['name']}-gen",
                },
            )

        router.post(f"{BASE_URL}/api/application.create").mock(side_effect=_create)
        github_route = router.post(f"{BASE_URL}/api/application.saveGithubProvider").mock(
            return_value=httpx.Response(200, json={})
        )
        router.post(f"{BASE_URL}/api/application.saveDockerProvider").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/application.saveBuildType").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/application.update").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/domain.create").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, web_app_config, state_file)

        # Verify repository is just the name, not "owner/repo"
        for call in github_route.calls:
            payload = json.loads(call.request.content)
            assert payload["repository"] == "your-app"
            assert "/" not in payload["repository"]
            assert payload["owner"] == "your-org"

    def test_github_provider_mismatch_exits(self, tmp_path, web_app_config):
        """Exits with error when no GitHub provider matches the configured owner."""
        router = respx.Router()
        router.post(f"{BASE_URL}/api/project.create").mock(
            return_value=httpx.Response(
                200,
                json={
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                },
            )
        )
        router.get(f"{BASE_URL}/api/github.githubProviders").mock(
            return_value=httpx.Response(200, json=[{"githubId": "gh-wrong"}])
        )
        router.get(f"{BASE_URL}/api/github.getGithubRepositories").mock(
            return_value=httpx.Response(
                200,
                json=[{"owner": {"login": "different-org"}, "name": "some-repo"}],
            )
        )
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        with pytest.raises(SystemExit, match="No GitHub provider"):
            dokploy.cmd_setup(client, web_app_config, state_file)


# ---------------------------------------------------------------------------
# cmd_env tests
# ---------------------------------------------------------------------------


class TestCmdEnv:
    def _write_state(self, state_file: Path, state: dict) -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

    def test_happy_path(self, tmp_path, minimal_config):
        """Pushes filtered .env to env_targets."""
        # Add env_targets to config
        minimal_config["project"]["env_targets"] = ["app"]

        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}},
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        # Write .env file
        (tmp_path / ".env").write_text("FOO=bar\nBAZ=qux\n")

        router = respx.Router()
        env_route = router.post(f"{BASE_URL}/api/application.saveEnvironment").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        dokploy.cmd_env(client, minimal_config, state_file, tmp_path)

        # Verify saveEnvironment was called
        assert env_route.called
        payload = json.loads(env_route.calls[0].request.content)
        assert payload["applicationId"] == "app-1"
        assert "FOO=bar" in payload["env"]
        assert payload["createEnvFile"] is False

    def test_create_env_file_flag(self, tmp_path, minimal_config):
        """Respects per-app create_env_file setting in saveEnvironment payload."""
        minimal_config["project"]["env_targets"] = ["app"]
        minimal_config["apps"][0]["create_env_file"] = True

        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}},
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        (tmp_path / ".env").write_text("FOO=bar\n")

        router = respx.Router()
        env_route = router.post(f"{BASE_URL}/api/application.saveEnvironment").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        dokploy.cmd_env(client, minimal_config, state_file, tmp_path)

        payload = json.loads(env_route.calls[0].request.content)
        assert payload["createEnvFile"] is True

    def test_missing_env_file(self, tmp_path, minimal_config):
        """Exits when .env file is missing and env_targets is set."""
        minimal_config["project"]["env_targets"] = ["app"]

        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}},
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        router = respx.Router()
        router.post(f"{BASE_URL}/api/application.saveEnvironment").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        with pytest.raises(SystemExit):
            dokploy.cmd_env(client, minimal_config, state_file, tmp_path)

    def test_custom_env_with_ref_resolution(self, tmp_path, web_app_config):
        """Per-app custom env with {ref} placeholders is resolved."""
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {
                "redis": {"applicationId": "app-redis", "appName": "redis-gen"},
                "web": {"applicationId": "app-web", "appName": "web-gen"},
                "worker": {"applicationId": "app-worker", "appName": "worker-gen"},
                "beat": {"applicationId": "app-beat", "appName": "beat-gen"},
                "flower": {"applicationId": "app-flower", "appName": "flower-gen"},
            },
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        # Write .env for env_targets
        (tmp_path / ".env").write_text("APP_URL=http://localhost\n")

        router = respx.Router()
        env_route = router.post(f"{BASE_URL}/api/application.saveEnvironment").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        dokploy.cmd_env(client, web_app_config, state_file, tmp_path)

        # flower has custom env with {redis} ref
        # Find the call for flower (last custom env push)
        flower_calls = [c for c in env_route.calls if json.loads(c.request.content)["applicationId"] == "app-flower"]
        assert len(flower_calls) == 1
        payload = json.loads(flower_calls[0].request.content)
        # {redis} should resolve to redis appName
        assert "redis-gen" in payload["env"]
        assert "{redis}" not in payload["env"]
        assert payload["createEnvFile"] is False

    def test_excluded_prefixes_filtered(self, tmp_path, minimal_config):
        """Env vars with excluded prefixes are filtered out."""
        minimal_config["project"]["env_targets"] = ["app"]

        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}},
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        (tmp_path / ".env").write_text("DOKPLOY_URL=x\nCOMPOSE_FILE=y\nAPP_KEY=secret\n")

        router = respx.Router()
        env_route = router.post(f"{BASE_URL}/api/application.saveEnvironment").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        dokploy.cmd_env(client, minimal_config, state_file, tmp_path)

        payload = json.loads(env_route.calls[0].request.content)
        assert "DOKPLOY_URL" not in payload["env"]
        assert "COMPOSE_FILE" not in payload["env"]
        assert "APP_KEY=secret" in payload["env"]


# ---------------------------------------------------------------------------
# cmd_trigger tests
# ---------------------------------------------------------------------------


class TestCmdTrigger:
    def _write_state(self, state_file: Path, state: dict) -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

    def test_deploys_in_wave_order(self, tmp_path, web_app_config):
        """Deploy calls application.deploy for each app in wave order."""
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {
                "redis": {"applicationId": "app-redis", "appName": "redis-gen"},
                "web": {"applicationId": "app-web", "appName": "web-gen"},
                "worker": {"applicationId": "app-worker", "appName": "worker-gen"},
                "beat": {"applicationId": "app-beat", "appName": "beat-gen"},
                "flower": {"applicationId": "app-flower", "appName": "flower-gen"},
            },
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        router = respx.Router()
        deploy_route = router.post(f"{BASE_URL}/api/application.deploy").mock(return_value=httpx.Response(200, content=b""))
        client = _make_client(router)

        dokploy.cmd_trigger(client, web_app_config, state_file)

        # Should be called once per app in deploy_order
        deploy_order = web_app_config["project"]["deploy_order"]
        total_apps = sum(len(wave) for wave in deploy_order)
        assert len(deploy_route.calls) == total_apps

        # Verify the order of applicationIds matches deploy_order
        called_ids = [json.loads(c.request.content)["applicationId"] for c in deploy_route.calls]
        expected_ids = []
        for wave in deploy_order:
            for name in wave:
                expected_ids.append(state["apps"][name]["applicationId"])
        assert called_ids == expected_ids

    def test_deploy_returns_empty_response(self, tmp_path, minimal_config):
        """application.deploy returns empty body per API quirk."""
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}},
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        router = respx.Router()
        router.post(f"{BASE_URL}/api/application.deploy").mock(return_value=httpx.Response(200, content=b""))
        client = _make_client(router)

        # Should not raise despite empty response
        dokploy.cmd_trigger(client, minimal_config, state_file)


# ---------------------------------------------------------------------------
# cmd_status tests
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def _write_state(self, state_file: Path, state: dict) -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

    def test_status_output(self, tmp_path, capsys):
        """Status displays application status for each app."""
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {
                "web": {"applicationId": "app-web", "appName": "web-gen"},
                "worker": {"applicationId": "app-worker", "appName": "worker-gen"},
            },
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        router = respx.Router()

        def _app_one(request):
            params = dict(request.url.params)
            app_id = params.get("applicationId", "")
            statuses = {
                "app-web": "running",
                "app-worker": "idle",
            }
            return httpx.Response(200, json={"applicationId": app_id, "applicationStatus": statuses.get(app_id, "unknown")})

        router.get(f"{BASE_URL}/api/application.one").mock(side_effect=_app_one)
        client = _make_client(router)

        dokploy.cmd_status(client, state_file)

        output = capsys.readouterr().out
        assert "running" in output
        assert "idle" in output
        assert "proj-1" in output


# ---------------------------------------------------------------------------
# cmd_destroy tests
# ---------------------------------------------------------------------------


class TestCmdDestroy:
    def _write_state(self, state_file: Path, state: dict) -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

    def test_happy_path(self, tmp_path):
        """Destroy calls project.remove and deletes state file."""
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}},
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        router = respx.Router()
        remove_route = router.post(f"{BASE_URL}/api/project.remove").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        dokploy.cmd_destroy(client, state_file)

        # Verify project.remove was called with correct projectId
        payload = json.loads(remove_route.calls[0].request.content)
        assert payload["projectId"] == "proj-1"

        # Verify state file deleted
        assert not state_file.exists()

    def test_uses_project_remove_not_delete(self, tmp_path):
        """API quirk: uses project.remove, not project.delete."""
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {},
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        self._write_state(state_file, state)

        router = respx.Router()
        remove_route = router.post(f"{BASE_URL}/api/project.remove").mock(return_value=httpx.Response(200, json={}))
        client = _make_client(router)

        dokploy.cmd_destroy(client, state_file)

        assert remove_route.called


# ---------------------------------------------------------------------------
# Full pipeline test
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_setup_env_deploy_status_destroy(self, tmp_path, web_app_config):
        """Full lifecycle: setup -> env -> deploy -> status -> destroy."""
        repo_root = tmp_path

        # Write .env file
        (repo_root / ".env").write_text("APP_KEY=secret123\nDATABASE_URL=postgres://localhost\n")

        router = respx.Router()
        ids = _setup_router(router, with_github=True, github_owner="your-org")

        # Additional routes for env, deploy, status, destroy
        router.post(f"{BASE_URL}/api/application.saveEnvironment").mock(return_value=httpx.Response(200, json={}))
        router.post(f"{BASE_URL}/api/application.deploy").mock(return_value=httpx.Response(200, content=b""))

        def _app_one(request):
            params = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "applicationId": params.get("applicationId", ""),
                    "applicationStatus": "running",
                },
            )

        router.get(f"{BASE_URL}/api/application.one").mock(side_effect=_app_one)
        router.post(f"{BASE_URL}/api/project.remove").mock(return_value=httpx.Response(200, json={}))

        client = _make_client(router)
        state_file = tmp_path / ".dokploy-state" / "test.json"

        # 1. Setup
        assert not state_file.exists()
        dokploy.cmd_setup(client, web_app_config, state_file)
        assert state_file.exists()

        state = json.loads(state_file.read_text())
        assert "projectId" in state
        assert len(state["apps"]) == 5

        # 2. Env
        dokploy.cmd_env(client, web_app_config, state_file, repo_root)

        # 3. Deploy
        dokploy.cmd_trigger(client, web_app_config, state_file)

        # 4. Status
        dokploy.cmd_status(client, state_file)

        # 5. Destroy — state file should be deleted
        dokploy.cmd_destroy(client, state_file)
        assert not state_file.exists()


def _make_project_all_response(
    project_name: str,
    project_id: str = "proj-existing-001",
    environment_id: str = "env-existing-001",
    apps: dict[str, dict] | None = None,
) -> list[dict]:
    """Build a mock project.all response with nested environments/applications."""
    if apps is None:
        apps = {}
    applications = [
        {
            "applicationId": info["applicationId"],
            "name": name,
            "appName": info["appName"],
        }
        for name, info in apps.items()
    ]
    return [
        {
            "projectId": project_id,
            "name": project_name,
            "description": "",
            "environments": [
                {
                    "environmentId": environment_id,
                    "applications": applications,
                }
            ],
        }
    ]


class TestCmdImport:
    def test_happy_path(self, tmp_path, minimal_config):
        """Import writes correct state file from existing project."""
        server_apps = {
            "app": {"applicationId": "app-srv-001", "appName": "app-generated"},
        }
        projects = _make_project_all_response("my-app", apps=server_apps)

        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=projects))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_import(client, minimal_config, state_file)

        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["projectId"] == "proj-existing-001"
        assert state["environmentId"] == "env-existing-001"
        assert state["apps"]["app"]["applicationId"] == "app-srv-001"
        assert state["apps"]["app"]["appName"] == "app-generated"

    def test_multi_app(self, tmp_path, web_app_config):
        """Import maps all apps from config to server-side applications."""
        server_apps = {
            "redis": {"applicationId": "app-r-001", "appName": "redis-gen"},
            "web": {"applicationId": "app-w-002", "appName": "web-gen"},
            "worker": {"applicationId": "app-k-003", "appName": "worker-gen"},
            "beat": {"applicationId": "app-b-004", "appName": "beat-gen"},
            "flower": {"applicationId": "app-f-005", "appName": "flower-gen"},
        }
        projects = _make_project_all_response("my-web-app", apps=server_apps)

        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=projects))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_import(client, web_app_config, state_file)

        state = json.loads(state_file.read_text())
        assert len(state["apps"]) == 5
        for name in ("redis", "web", "worker", "beat", "flower"):
            assert name in state["apps"]
            assert "applicationId" in state["apps"][name]
            assert "appName" in state["apps"][name]

    def test_no_matching_project(self, tmp_path, minimal_config):
        """Exits with error when no project matches the config name."""
        projects = _make_project_all_response("other-project")

        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=projects))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        with pytest.raises(SystemExit):
            dokploy.cmd_import(client, minimal_config, state_file)

    def test_state_file_already_exists(self, tmp_path, minimal_config):
        """Exits with error when state file already exists."""
        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=[]))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("{}")

        with pytest.raises(SystemExit):
            dokploy.cmd_import(client, minimal_config, state_file)

    def test_missing_app_on_server(self, tmp_path, minimal_config):
        """Exits with error when a config app is not found on the server."""
        # Server has no applications
        projects = _make_project_all_response("my-app", apps={})

        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=projects))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        with pytest.raises(SystemExit):
            dokploy.cmd_import(client, minimal_config, state_file)

    def test_empty_project_list(self, tmp_path, minimal_config):
        """Exits with error when server has no projects."""
        router = respx.Router()
        router.get(f"{BASE_URL}/api/project.all").mock(return_value=httpx.Response(200, json=[]))
        client = _make_client(router)

        state_file = tmp_path / ".dokploy-state" / "test.json"
        with pytest.raises(SystemExit):
            dokploy.cmd_import(client, minimal_config, state_file)


# ---------------------------------------------------------------------------
# resolve_github_provider tests
# ---------------------------------------------------------------------------


class TestResolveGithubProvider:
    def test_selects_matching_provider(self):
        """Picks the provider whose repos include the configured owner."""
        router = respx.Router()
        router.get(f"{BASE_URL}/api/github.getGithubRepositories").mock(
            side_effect=lambda request: (
                httpx.Response(
                    200,
                    json=[{"owner": {"login": "wrong-org"}, "name": "repo-a"}],
                )
                if request.url.params.get("githubId") == "gh-wrong"
                else httpx.Response(
                    200,
                    json=[{"owner": {"login": "my-org"}, "name": "repo-b"}],
                )
            )
        )
        client = _make_client(router)

        providers = [
            {"githubId": "gh-wrong"},
            {"githubId": "gh-right"},
        ]
        result = dokploy.resolve_github_provider(client, providers, "my-org")
        assert result == "gh-right"

    def test_raises_when_no_provider_matches(self):
        """Exits with clear error when no provider has repos for the owner."""
        router = respx.Router()
        router.get(f"{BASE_URL}/api/github.getGithubRepositories").mock(
            return_value=httpx.Response(
                200,
                json=[{"owner": {"login": "other-org"}, "name": "repo-x"}],
            )
        )
        client = _make_client(router)

        providers = [{"githubId": "gh-1"}, {"githubId": "gh-2"}]
        with pytest.raises(SystemExit, match="No GitHub provider"):
            dokploy.resolve_github_provider(client, providers, "target-org")

    def test_first_match_wins(self):
        """Returns the first provider that matches, not scanning further."""
        call_count = {"n": 0}

        def _mock_repos(request):
            call_count["n"] += 1
            return httpx.Response(
                200,
                json=[{"owner": {"login": "my-org"}, "name": "repo"}],
            )

        router = respx.Router()
        router.get(f"{BASE_URL}/api/github.getGithubRepositories").mock(side_effect=_mock_repos)
        client = _make_client(router)

        providers = [{"githubId": "gh-1"}, {"githubId": "gh-2"}]
        result = dokploy.resolve_github_provider(client, providers, "my-org")
        assert result == "gh-1"
        assert call_count["n"] == 1
