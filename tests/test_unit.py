"""Unit tests for pure functions in main.py."""

import argparse
import httpx
import icarus as dokploy
import json
import pytest
import respx
import sys
import yaml
from icarus import commands as icarus_commands, ssh as icarus_ssh
from pathlib import Path
from unittest.mock import MagicMock, call, patch

pytestmark = pytest.mark.unit


class TestBuildConfig:
    def test_missing_env_file_falls_back_to_env_vars(self, tmp_path, monkeypatch):
        """_build_config returns a working Config even when .env does not exist."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOKPLOY_URL", "https://test.example.com")
        cfg = dokploy._build_config()
        assert cfg("DOKPLOY_URL") == "https://test.example.com"

    def test_reads_env_file_when_present(self, tmp_path, monkeypatch):
        """_build_config reads values from .env when the file exists."""
        (tmp_path / ".env").write_text("MY_TEST_VAR=from_file\n")
        monkeypatch.chdir(tmp_path)
        cfg = dokploy._build_config()
        assert cfg("MY_TEST_VAR") == "from_file"

    def test_dotenv_file_env_var_overrides_default_path(self, tmp_path, monkeypatch):
        """DOTENV_FILE env var makes _build_config read from that file instead."""
        custom = tmp_path / "custom.env"
        custom.write_text("CUSTOM_VAR=from_custom\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOTENV_FILE", str(custom))
        cfg = dokploy._build_config()
        assert cfg("CUSTOM_VAR") == "from_custom"


class TestFindRepoRoot:
    def test_finds_repo_root(self, tmp_path, monkeypatch):
        """find_repo_root walks up from cwd and returns the dir containing dokploy.yml."""
        (tmp_path / "dokploy.yml").write_text("project: {}")
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        assert dokploy.find_repo_root() == tmp_path

    def test_exits_when_not_found(self, tmp_path, monkeypatch):
        """find_repo_root exits when no dokploy.yml is found."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            dokploy.find_repo_root()


class TestLoadConfig:
    def test_loads_valid_yaml(self, tmp_path, minimal_config):
        cfg_file = tmp_path / "dokploy.yml"
        cfg_file.write_text(yaml.dump(minimal_config))
        result = dokploy.load_config(tmp_path)
        assert result["project"]["name"] == "my-app"

    def test_exits_on_missing_file(self, tmp_path):
        with pytest.raises(SystemExit):
            dokploy.load_config(tmp_path)


class TestValidateConfig:
    def test_valid_config_passes(self, web_app_config):
        # Should not raise
        dokploy.validate_config(web_app_config)

    def test_unknown_app_in_env_targets_exits(self, minimal_config):
        minimal_config["project"]["env_targets"] = ["nonexistent"]
        with pytest.raises(SystemExit):
            dokploy.validate_config(minimal_config)

    def test_unknown_app_in_deploy_order_exits(self, minimal_config):
        minimal_config["project"]["deploy_order"] = [["nonexistent"]]
        with pytest.raises(SystemExit):
            dokploy.validate_config(minimal_config)

    def test_missing_github_section_exits(self, minimal_config):
        minimal_config["apps"].append({"name": "api", "source": "github"})
        minimal_config["project"]["deploy_order"] = [["app", "api"]]
        with pytest.raises(SystemExit):
            dokploy.validate_config(minimal_config)

    def test_github_section_present_passes(self, minimal_config):
        minimal_config["apps"].append({"name": "api", "source": "github"})
        minimal_config["project"]["deploy_order"] = [["app", "api"]]
        minimal_config["github"] = {
            "owner": "org",
            "repository": "repo",
            "branch": "main",
        }
        dokploy.validate_config(minimal_config)


class TestValidateEnvReferences:
    def test_valid_refs_pass(self, web_app_config):
        dokploy.validate_env_references(web_app_config)

    def test_unknown_app_in_env_overrides_exits(self, minimal_config):
        minimal_config["environments"] = {"prod": {"apps": {"bogus": {"dockerImage": "x"}}}}
        with pytest.raises(SystemExit):
            dokploy.validate_env_references(minimal_config)

    def test_no_environments_passes(self, minimal_config):
        minimal_config.pop("environments", None)
        dokploy.validate_env_references(minimal_config)


class TestMergeEnvOverrides:
    def test_github_overrides_merge(self, web_app_config):
        merged = dokploy.merge_env_overrides(web_app_config, "dev")
        assert merged["github"]["branch"] == "develop"
        # Owner should be preserved from base
        assert merged["github"]["owner"] == "your-org"

    def test_per_app_overrides_merge(self, web_app_config):
        merged = dokploy.merge_env_overrides(web_app_config, "prod")
        web = next(a for a in merged["apps"] if a["name"] == "web")
        assert web["domain"]["host"] == "app.example.com"

    def test_missing_env_returns_base(self, minimal_config):
        merged = dokploy.merge_env_overrides(minimal_config, "nonexistent")
        assert merged["project"]["name"] == "my-app"
        assert "environments" not in merged

    def test_original_config_unchanged(self, web_app_config):
        import copy

        original = copy.deepcopy(web_app_config)
        dokploy.merge_env_overrides(web_app_config, "dev")
        assert web_app_config == original


class TestResolveRefs:
    def test_single_ref(self, sample_state):
        result = dokploy.resolve_refs("redis://{redis}:6379/0", sample_state)
        assert result == "redis://redis-abc123:6379/0"

    def test_multiple_refs(self, sample_state):
        result = dokploy.resolve_refs("{redis} and {web}", sample_state)
        assert result == "redis-abc123 and web-def456"

    def test_unknown_ref_left_as_is(self, sample_state):
        result = dokploy.resolve_refs("{unknown}", sample_state)
        assert result == "{unknown}"

    def test_no_refs_unchanged(self, sample_state):
        result = dokploy.resolve_refs("plain string", sample_state)
        assert result == "plain string"


class TestGetEnvExcludes:
    def test_defaults_only(self, monkeypatch):
        monkeypatch.delenv("ENV_EXCLUDE_PREFIXES", raising=False)
        monkeypatch.delenv("ENV_EXCLUDES", raising=False)
        patterns = dokploy.get_env_excludes()
        assert "COMPOSE_" in patterns
        assert "DOKPLOY_" in patterns

    def test_with_legacy_env_var(self, monkeypatch):
        monkeypatch.delenv("ENV_EXCLUDES", raising=False)
        monkeypatch.setenv("ENV_EXCLUDE_PREFIXES", "MY_SECRET_,INTERNAL_")
        patterns = dokploy.get_env_excludes()
        assert "MY_SECRET_" in patterns
        assert "INTERNAL_" in patterns
        assert "COMPOSE_" in patterns

    def test_with_new_env_var(self, monkeypatch):
        monkeypatch.delenv("ENV_EXCLUDE_PREFIXES", raising=False)
        monkeypatch.setenv("ENV_EXCLUDES", "DEV,SECRET*")
        patterns = dokploy.get_env_excludes()
        assert "DEV" in patterns
        assert "SECRET*" in patterns
        assert "COMPOSE_" in patterns

    def test_empty_extras(self, monkeypatch):
        monkeypatch.setenv("ENV_EXCLUDE_PREFIXES", "")
        monkeypatch.setenv("ENV_EXCLUDES", "")
        patterns = dokploy.get_env_excludes()
        assert patterns == dokploy.DEFAULT_ENV_EXCLUDES


class TestFilterEnv:
    def test_comments_removed(self):
        content = "# comment\nFOO=bar\n"
        result = dokploy.filter_env(content, [])
        assert "# comment" not in result
        assert "FOO=bar" in result

    def test_blank_lines_removed(self):
        content = "\n\nFOO=bar\n\n"
        result = dokploy.filter_env(content, [])
        assert result == "FOO=bar\n"

    def test_excluded_prefixes_filtered(self):
        content = "COMPOSE_FILE=x\nAPP_URL=y\n"
        result = dokploy.filter_env(content, ["COMPOSE_"])
        assert "COMPOSE_FILE" not in result
        assert "APP_URL=y" in result

    def test_valid_lines_kept(self):
        content = "A=1\nB=2\nC=3\n"
        result = dokploy.filter_env(content, [])
        assert result == "A=1\nB=2\nC=3\n"

    def test_trailing_newline(self):
        content = "A=1"
        result = dokploy.filter_env(content, [])
        assert result.endswith("\n")

    def test_empty_after_filtering(self):
        content = "# just a comment\n"
        result = dokploy.filter_env(content, [])
        assert result == ""

    def test_exact_match_excludes_only_exact_key(self):
        content = "DEV=True\nDEVICE_TOKEN=abc\nDEVELOPER_MODE=on\n"
        result = dokploy.filter_env(content, ["DEV"])
        assert "DEV=True" not in result
        assert "DEVICE_TOKEN=abc" in result
        assert "DEVELOPER_MODE=on" in result

    def test_prefix_match_with_trailing_underscore(self):
        content = "COMPOSE_FILE=x\nCOMPOSE_PROJECT=y\nCOMPOSER=z\n"
        result = dokploy.filter_env(content, ["COMPOSE_"])
        assert "COMPOSE_FILE" not in result
        assert "COMPOSE_PROJECT" not in result
        assert "COMPOSER=z" in result

    def test_prefix_match_with_trailing_wildcard(self):
        content = "SECRET_KEY=abc\nSECRET_TOKEN=xyz\nSECURE=yes\n"
        result = dokploy.filter_env(content, ["SECRET*"])
        assert "SECRET_KEY" not in result
        assert "SECRET_TOKEN" not in result
        assert "SECURE=yes" in result

    def test_mixed_exact_and_prefix_patterns(self):
        content = "DEV=True\nDEVICE=phone\nCOMPOSE_FILE=x\nDEBUG=1\n"
        result = dokploy.filter_env(content, ["DEV", "COMPOSE_", "DEBUG"])
        assert "DEV=True" not in result
        assert "DEVICE=phone" in result
        assert "COMPOSE_FILE" not in result
        assert "DEBUG=1" not in result

    def test_empty_pattern_list(self):
        content = "A=1\nB=2\n"
        result = dokploy.filter_env(content, [])
        assert result == "A=1\nB=2\n"

    def test_exact_match_case_sensitive(self):
        content = "dev=true\nDEV=True\n"
        result = dokploy.filter_env(content, ["DEV"])
        assert "DEV=True" not in result
        assert "dev=true" in result


class TestResolveEnvForPush:
    def test_file_values_pass_through(self, tmp_path):
        """Keys from file are returned when no matching env vars are set."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        result = dokploy.resolve_env_for_push(env_file, [])
        assert "FOO=bar" in result
        assert "BAZ=qux" in result

    def test_os_environ_wins_over_file(self, tmp_path, monkeypatch):
        """Process env value overrides file value for the same key."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=from_file\n")
        monkeypatch.setenv("FOO", "from_process")
        result = dokploy.resolve_env_for_push(env_file, [])
        assert "FOO=from_process" in result
        assert "from_file" not in result

    def test_excluded_keys_filtered(self, tmp_path):
        """Excluded keys are not included in output."""
        env_file = tmp_path / ".env"
        env_file.write_text("DOKPLOY_KEY=secret\nAPP_URL=http://app\n")
        result = dokploy.resolve_env_for_push(env_file, ["DOKPLOY_"])
        assert "DOKPLOY_KEY" not in result
        assert "APP_URL=http://app" in result

    def test_stray_env_vars_not_added(self, tmp_path, monkeypatch):
        """Keys in os.environ but not in the file are not added to output."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")
        monkeypatch.setenv("STRAY_VAR", "should_not_appear")
        result = dokploy.resolve_env_for_push(env_file, [])
        assert "STRAY_VAR" not in result

    def test_value_with_equals_sign(self, tmp_path, monkeypatch):
        """Values containing '=' are handled correctly via os.environ override."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_URL=old\n")
        monkeypatch.setenv("DB_URL", "postgres://u:p@h/db?opt=1")
        result = dokploy.resolve_env_for_push(env_file, [])
        assert "DB_URL=postgres://u:p@h/db?opt=1" in result

    def test_empty_file_returns_empty(self, tmp_path):
        """An empty .env file produces an empty string."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        result = dokploy.resolve_env_for_push(env_file, [])
        assert result == ""

    def test_comments_and_blanks_skipped(self, tmp_path):
        """Comments and blank lines in the file are not included."""
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nFOO=bar\n")
        result = dokploy.resolve_env_for_push(env_file, [])
        assert "# comment" not in result
        assert "FOO=bar" in result


class TestLoadSaveState:
    def test_round_trip(self, tmp_path, sample_state):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.save_state(sample_state, state_file)
        loaded = dokploy.load_state(state_file)
        assert loaded == sample_state

    def test_load_missing_file_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            dokploy.load_state(tmp_path / "nonexistent.json")

    def test_save_creates_directory(self, tmp_path, sample_state):
        state_file = tmp_path / "deep" / "nested" / "state.json"
        dokploy.save_state(sample_state, state_file)
        assert state_file.exists()
        assert json.loads(state_file.read_text()) == sample_state


class TestArgparse:
    def test_valid_commands(self):
        for cmd in ["check", "setup", "env", "apply", "trigger", "status", "destroy", "import"]:
            args = argparse.ArgumentParser()
            args.add_argument("--env", default=None)
            args.add_argument(
                "command",
                choices=["check", "setup", "env", "apply", "trigger", "status", "destroy", "import"],
            )
            parsed = args.parse_args(["--env", "prod", cmd])
            assert parsed.command == cmd
            assert parsed.env == "prod"

    def test_invalid_command_rejected(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--env", default=None)
        parser.add_argument(
            "command",
            choices=["check", "setup", "env", "apply", "trigger", "status", "destroy", "import"],
        )
        with pytest.raises(SystemExit):
            parser.parse_args(["bogus"])

    def test_env_flag_parsed(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--env", default=None)
        parser.add_argument(
            "command",
            choices=["check", "setup", "env", "apply", "trigger", "status", "destroy", "import"],
        )
        parsed = parser.parse_args(["--env", "staging", "apply"])
        assert parsed.env == "staging"
        assert parsed.command == "apply"


class TestGetStateFile:
    def test_returns_correct_path(self, tmp_path):
        result = dokploy.get_state_file(tmp_path, "prod")
        assert result == tmp_path / ".dokploy-state" / "prod.json"


class TestBuildGithubProviderPayload:
    def test_defaults(self):
        """Minimal app_def produces default buildPath, triggerType, watchPaths."""
        result = dokploy.build_github_provider_payload(
            "app-1",
            {"name": "web", "source": "github"},
            {"owner": "org", "repository": "repo", "branch": "main"},
            "gh-123",
        )
        assert result == {
            "applicationId": "app-1",
            "repository": "repo",
            "branch": "main",
            "owner": "org",
            "buildPath": "/",
            "githubId": "gh-123",
            "enableSubmodules": False,
            "triggerType": "push",
            "watchPaths": None,
        }

    def test_custom_values(self):
        """Per-app overrides for buildPath, triggerType, watchPaths are passed through."""
        app_def = {
            "name": "web",
            "source": "github",
            "buildPath": "/app",
            "triggerType": "manual",
            "watchPaths": ["src/**", "Dockerfile"],
        }
        result = dokploy.build_github_provider_payload(
            "app-1",
            app_def,
            {"owner": "org", "repository": "repo", "branch": "main"},
            "gh-123",
        )
        assert result["buildPath"] == "/app"
        assert result["triggerType"] == "manual"
        assert result["watchPaths"] == ["src/**", "Dockerfile"]


class TestBuildBuildTypePayload:
    def test_default_dockerfile(self):
        """No buildType defaults to dockerfile with Dockerfile defaults."""
        result = dokploy.build_build_type_payload("app-1", {"name": "web"})
        assert result == {
            "applicationId": "app-1",
            "buildType": "dockerfile",
            "dockerfile": "Dockerfile",
            "dockerContextPath": "",
            "dockerBuildStage": "",
            "herokuVersion": None,
            "railpackVersion": None,
        }

    def test_explicit_dockerfile(self):
        """Explicit dockerfile settings are passed through."""
        app_def = {
            "name": "web",
            "buildType": "dockerfile",
            "dockerfile": "docker/Dockerfile.prod",
            "dockerContextPath": ".",
            "dockerBuildStage": "production",
        }
        result = dokploy.build_build_type_payload("app-1", app_def)
        assert result["dockerfile"] == "docker/Dockerfile.prod"
        assert result["dockerContextPath"] == "."
        assert result["dockerBuildStage"] == "production"

    def test_static_build_type(self):
        """Static buildType includes publishDirectory, SPA flag, and required API fields."""
        app_def = {
            "name": "site",
            "buildType": "static",
            "publishDirectory": "dist",
        }
        result = dokploy.build_build_type_payload("app-1", app_def)
        assert result == {
            "applicationId": "app-1",
            "buildType": "static",
            "dockerfile": None,
            "publishDirectory": "dist",
            "isStaticSpa": False,
            "dockerContextPath": "",
            "dockerBuildStage": "",
            "herokuVersion": None,
            "railpackVersion": None,
        }

    def test_static_default_publish_directory(self):
        """Static buildType without publishDirectory defaults to empty string."""
        result = dokploy.build_build_type_payload("app-1", {"name": "site", "buildType": "static"})
        assert result["publishDirectory"] == ""

    def test_nixpacks_build_type(self):
        """Nixpacks buildType includes required API fields only."""
        result = dokploy.build_build_type_payload("app-1", {"name": "app", "buildType": "nixpacks"})
        assert result == {
            "applicationId": "app-1",
            "buildType": "nixpacks",
            "dockerfile": None,
            "dockerContextPath": "",
            "dockerBuildStage": "",
            "herokuVersion": None,
            "railpackVersion": None,
        }

    def test_all_build_types_include_required_api_fields(self):
        """Dokploy API requires dockerfile, dockerContextPath, dockerBuildStage, herokuVersion, railpackVersion for all build types."""
        for build_type in ("dockerfile", "static", "nixpacks", "heroku_buildpacks"):
            result = dokploy.build_build_type_payload("app-1", {"name": "x", "buildType": build_type})
            assert "dockerfile" in result, f"missing dockerfile for {build_type}"
            assert "dockerContextPath" in result, f"missing dockerContextPath for {build_type}"
            assert "dockerBuildStage" in result, f"missing dockerBuildStage for {build_type}"
            assert "herokuVersion" in result, f"missing herokuVersion for {build_type}"
            assert "railpackVersion" in result, f"missing railpackVersion for {build_type}"

    def test_static_spa_included_when_true(self):
        """Static build with SPA=true passes isStaticSpa to the API."""
        app_def = {
            "name": "site",
            "buildType": "static",
            "publishDirectory": "dist",
            "isStaticSpa": True,
        }
        result = dokploy.build_build_type_payload("app-1", app_def)
        assert result["isStaticSpa"] is True

    def test_static_spa_defaults_false(self):
        """Static build without explicit SPA defaults to false."""
        app_def = {"name": "site", "buildType": "static"}
        result = dokploy.build_build_type_payload("app-1", app_def)
        assert result.get("isStaticSpa") is False


class TestBuildDomainPayload:
    def test_required_fields_only(self):
        """Minimal domain with only required fields."""
        dom = {
            "host": "example.com",
            "port": 8080,
            "https": True,
            "certificateType": "letsencrypt",
        }
        result = dokploy.build_domain_payload("app-1", dom)
        assert result == {
            "applicationId": "app-1",
            "host": "example.com",
            "port": 8080,
            "https": True,
            "certificateType": "letsencrypt",
        }
        assert "path" not in result
        assert "internalPath" not in result
        assert "stripPath" not in result

    def test_with_path(self):
        """Domain with path field is included in payload."""
        dom = {
            "host": "example.com",
            "port": 80,
            "https": True,
            "certificateType": "letsencrypt",
            "path": "/docs",
        }
        result = dokploy.build_domain_payload("app-1", dom)
        assert result["path"] == "/docs"

    def test_all_optional_fields(self):
        """All optional domain fields (path, internalPath, stripPath) included."""
        dom = {
            "host": "example.com",
            "port": 80,
            "https": True,
            "certificateType": "letsencrypt",
            "path": "/api",
            "internalPath": "/v2",
            "stripPath": True,
        }
        result = dokploy.build_domain_payload("app-1", dom)
        assert result["path"] == "/api"
        assert result["internalPath"] == "/v2"
        assert result["stripPath"] is True


class TestBuildAppSettingsPayload:
    def test_no_settings_returns_none(self):
        """App with no autoDeploy/replicas returns None."""
        result = dokploy.build_app_settings_payload("app-1", {"name": "web"})
        assert result is None

    def test_auto_deploy_only(self):
        """Only autoDeploy set."""
        result = dokploy.build_app_settings_payload("app-1", {"name": "web", "autoDeploy": True})
        assert result == {"applicationId": "app-1", "autoDeploy": True}

    def test_replicas_only(self):
        """Only replicas set."""
        result = dokploy.build_app_settings_payload("app-1", {"name": "web", "replicas": 3})
        assert result == {"applicationId": "app-1", "replicas": 3}

    def test_both_settings(self):
        """Both autoDeploy and replicas set."""
        result = dokploy.build_app_settings_payload("app-1", {"name": "web", "autoDeploy": False, "replicas": 2})
        assert result == {
            "applicationId": "app-1",
            "autoDeploy": False,
            "replicas": 2,
        }

    def test_auto_deploy_false_is_included(self):
        """autoDeploy=False is explicitly included (not treated as falsy)."""
        result = dokploy.build_app_settings_payload("app-1", {"name": "web", "autoDeploy": False})
        assert result is not None
        assert result["autoDeploy"] is False


class TestBuildMountPayload:
    def test_volume_mount(self):
        """Volume mount produces correct API payload."""
        mount = {"source": "app_data", "target": "/data", "type": "volume"}
        result = dokploy.build_mount_payload("app-1", mount)
        assert result == {
            "serviceId": "app-1",
            "type": "volume",
            "volumeName": "app_data",
            "mountPath": "/data",
            "serviceType": "application",
        }

    def test_bind_mount(self):
        """Bind mount produces correct API payload."""
        mount = {"source": "/host/data", "target": "/container/data", "type": "bind"}
        result = dokploy.build_mount_payload("app-1", mount)
        assert result == {
            "serviceId": "app-1",
            "type": "bind",
            "hostPath": "/host/data",
            "mountPath": "/container/data",
            "serviceType": "application",
        }

    def test_volume_mount_uses_volume_name_not_host_path(self):
        """Volume mount sets volumeName, not hostPath."""
        mount = {"source": "my_vol", "target": "/mnt", "type": "volume"}
        result = dokploy.build_mount_payload("app-1", mount)
        assert "volumeName" in result
        assert "hostPath" not in result

    def test_bind_mount_uses_host_path_not_volume_name(self):
        """Bind mount sets hostPath, not volumeName."""
        mount = {"source": "/host/path", "target": "/mnt", "type": "bind"}
        result = dokploy.build_mount_payload("app-1", mount)
        assert "hostPath" in result
        assert "volumeName" not in result


class TestMergeEnvOverridesNewFields:
    def test_build_type_override_merges(self, github_static_config):
        """Environment override for autoDeploy/replicas merges into app."""
        merged = dokploy.merge_env_overrides(github_static_config, "prod")
        app = next(a for a in merged["apps"] if a["name"] == "app")
        # Base has autoDeploy=False, prod override has autoDeploy=True
        assert app["autoDeploy"] is True
        # Base has replicas=1, prod override has replicas=2
        assert app["replicas"] == 2

    def test_domain_override_merges(self, github_static_config):
        """Environment domain override merges into app."""
        expected = github_static_config["environments"]["prod"]["apps"]["app"]["domain"]
        merged = dokploy.merge_env_overrides(github_static_config, "prod")
        app = next(a for a in merged["apps"] if a["name"] == "app")
        assert app["domain"]["host"] == expected["host"]
        assert app["domain"]["port"] == expected["port"]
        assert app["domain"]["https"] == expected["https"]
        assert "path" not in app["domain"]

    def test_base_build_type_preserved(self, github_static_config):
        """buildType from base config is preserved when not overridden."""
        merged = dokploy.merge_env_overrides(github_static_config, "prod")
        app = next(a for a in merged["apps"] if a["name"] == "app")
        assert app["buildType"] == "static"

    def test_watch_paths_in_config(self, github_dockerfile_config):
        """watchPaths from base config are preserved through merge."""
        merged = dokploy.merge_env_overrides(github_dockerfile_config, "prod")
        app = next(a for a in merged["apps"] if a["name"] == "app")
        assert app["watchPaths"] == [
            "config/**",
            "assets/**",
            "Dockerfile",
            "^(?!.*\\.md$).*$",
        ]


class TestValidateConfigNewFixtures:
    def test_static_config_valid(self, github_static_config):
        dokploy.validate_config(github_static_config)

    def test_dockerfile_config_valid(self, github_dockerfile_config):
        dokploy.validate_config(github_dockerfile_config)

    def test_volumes_config_valid(self, volumes_config):
        dokploy.validate_config(volumes_config)

    def test_volumes_parsed_correctly(self, volumes_config):
        db = next(a for a in volumes_config["apps"] if a["name"] == "db")
        assert len(db["volumes"]) == 1
        assert db["volumes"][0]["type"] == "volume"
        assert db["volumes"][0]["source"] == "pg_data"
        assert db["volumes"][0]["target"] == "/var/lib/postgresql/data"

        app = next(a for a in volumes_config["apps"] if a["name"] == "app")
        assert len(app["volumes"]) == 2
        assert app["volumes"][0]["type"] == "volume"
        assert app["volumes"][1]["type"] == "bind"


class TestUnifiedApply:
    def test_trigger_command_accepted(self):
        """'trigger' is a valid CLI choice."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--env", default=None)
        parser.add_argument(
            "command",
            choices=["check", "setup", "env", "apply", "trigger", "status", "destroy", "import"],
        )
        parsed = parser.parse_args(["trigger"])
        assert parsed.command == "trigger"

    def test_apply_still_valid_command(self):
        """'apply' remains a valid CLI choice."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--env", default=None)
        parser.add_argument(
            "command",
            choices=["check", "setup", "env", "apply", "trigger", "status", "destroy", "import"],
        )
        parsed = parser.parse_args(["apply"])
        assert parsed.command == "apply"

    def test_cmd_trigger_exists(self):
        """dokploy.cmd_trigger is callable."""
        assert callable(dokploy.cmd_trigger)

    def test_fresh_project_runs_all_phases(self, tmp_path, monkeypatch):
        """Fresh project (no state file) runs check, setup, env, trigger in order."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: calls.append("check"))
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: calls.append("setup"))
        monkeypatch.setattr(
            icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: calls.append("env")
        )
        monkeypatch.setattr(
            icarus_commands,
            "cmd_trigger",
            lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: calls.append("trigger"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert calls == ["check", "setup", "env", "trigger"]

    def test_existing_project_skips_setup(self, tmp_path, monkeypatch):
        """Existing project (state file present) skips setup."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("{}")

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: calls.append("check"))
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: calls.append("setup"))
        monkeypatch.setattr(
            icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: calls.append("env")
        )
        monkeypatch.setattr(
            icarus_commands,
            "cmd_trigger",
            lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: calls.append("trigger"),
        )
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "setup" not in calls
        assert calls == ["check", "env", "trigger"]

    def test_existing_project_passes_redeploy_true(self, tmp_path, monkeypatch):
        """Existing project passes redeploy=True to cmd_trigger."""
        trigger_kwargs = {}
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("{}")

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands,
            "cmd_trigger",
            lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: trigger_kwargs.update(
                redeploy=redeploy
            ),
        )
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert trigger_kwargs["redeploy"] is True

    def test_fresh_project_passes_redeploy_false(self, tmp_path, monkeypatch):
        """Fresh project passes redeploy=False to cmd_trigger."""
        trigger_kwargs = {}
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands,
            "cmd_trigger",
            lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: trigger_kwargs.update(
                redeploy=redeploy
            ),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert trigger_kwargs["redeploy"] is False

    def test_phase_headers_printed(self, tmp_path, monkeypatch, capsys):
        """Each phase prints a header like '==> Phase N/4: ...'."""
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        output = capsys.readouterr().out
        assert "==> Phase 1/4: check" in output
        assert "==> Phase 2/4: setup" in output
        assert "==> Phase 3/4: env" in output
        assert "==> Phase 4/4: trigger" in output

    def test_check_failure_stops_execution(self, tmp_path, monkeypatch):
        """If cmd_check raises SystemExit, subsequent phases are not called."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        def failing_check(repo_root):
            calls.append("check")
            raise SystemExit(1)

        monkeypatch.setattr(icarus_commands, "cmd_check", failing_check)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: calls.append("setup"))
        monkeypatch.setattr(
            icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: calls.append("env")
        )
        monkeypatch.setattr(
            icarus_commands,
            "cmd_trigger",
            lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: calls.append("trigger"),
        )

        with pytest.raises(SystemExit):
            dokploy.cmd_apply(
                repo_root=tmp_path,
                client="fake-client",
                cfg={"project": {}},
                state_file=state_file,
            )

        assert calls == ["check"]


class TestCleanupStaleRoutes:
    def test_identifies_stale_configs(self):
        """Finds traefik configs that route to our domain but belong to old app names."""
        current_app_names = {"app-new-abc123"}
        domains = {"example.com"}
        traefik_files = {
            "app-old-xyz789": "Host(`example.com`)",
            "app-new-abc123": "Host(`example.com`)",
            "app-other-def456": "Host(`other.com`)",
        }
        stale = dokploy.find_stale_app_names(current_app_names, domains, traefik_files)
        assert stale == {"app-old-xyz789"}

    def test_no_stale_when_only_current(self):
        """No stale configs when only the current app routes to our domain."""
        current_app_names = {"app-current-abc123"}
        domains = {"example.com"}
        traefik_files = {
            "app-current-abc123": "Host(`example.com`)",
        }
        stale = dokploy.find_stale_app_names(current_app_names, domains, traefik_files)
        assert stale == set()

    def test_multiple_domains(self):
        """Detects stale configs across multiple domains."""
        current_app_names = {"app-web-abc", "app-api-def"}
        domains = {"web.example.com", "api.example.com"}
        traefik_files = {
            "app-web-abc": "Host(`web.example.com`)",
            "app-api-def": "Host(`api.example.com`)",
            "app-old-web-xyz": "Host(`web.example.com`)",
            "app-old-api-uvw": "Host(`api.example.com`)",
            "app-unrelated-zzz": "Host(`unrelated.com`)",
        }
        stale = dokploy.find_stale_app_names(current_app_names, domains, traefik_files)
        assert stale == {"app-old-web-xyz", "app-old-api-uvw"}

    def test_ignores_non_app_configs(self):
        """Skips system configs like dokploy.yml and middlewares.yml."""
        current_app_names = {"app-new-abc"}
        domains = {"example.com"}
        traefik_files = {
            "app-new-abc": "Host(`example.com`)",
            "dokploy": "Host(`dokploy.example.com`)",
            "middlewares": "",
        }
        stale = dokploy.find_stale_app_names(current_app_names, domains, traefik_files)
        assert stale == set()

    def test_empty_domains_returns_empty(self):
        """No cleanup needed when no domains are configured."""
        stale = dokploy.find_stale_app_names({"app-abc"}, set(), {})
        assert stale == set()

    def test_collect_domains_from_config(self):
        """Extracts domains from app definitions."""
        cfg = {
            "apps": [
                {
                    "name": "web",
                    "domain": {"host": "web.example.com", "port": 80, "https": True, "certificateType": "letsencrypt"},
                },
                {
                    "name": "api",
                    "domain": [
                        {"host": "api.example.com", "port": 80, "https": True, "certificateType": "letsencrypt"},
                        {"host": "api2.example.com", "port": 80, "https": True, "certificateType": "letsencrypt"},
                    ],
                },
                {"name": "worker"},
            ],
        }
        domains = dokploy.collect_domains(cfg)
        assert domains == {"web.example.com", "api.example.com", "api2.example.com"}

    def test_cleanup_called_during_redeploy(self, tmp_path, monkeypatch):
        """cmd_apply calls cleanup_stale_routes when redeploying."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("{}")

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)
        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda state, cfg: calls.append("cleanup"))

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "cleanup" in calls

    def test_cleanup_not_called_on_fresh_apply(self, tmp_path, monkeypatch):
        """cmd_apply does not call cleanup_stale_routes on fresh apply."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda state, cfg: calls.append("cleanup"))

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "cleanup" not in calls

    def test_cleanup_skipped_when_no_ssh_config(self, monkeypatch, capsys):
        """cleanup_stale_routes skips gracefully when DOKPLOY_SSH_HOST is not set."""
        monkeypatch.setattr(icarus_ssh, "config", MagicMock(side_effect=lambda key, default="": default))

        state = {"apps": {"web": {"appName": "app-web-abc"}}}
        cfg = {
            "apps": [
                {"name": "web", "domain": {"host": "example.com", "port": 80, "https": True, "certificateType": "letsencrypt"}}
            ]
        }

        dokploy.cleanup_stale_routes(state, cfg)

        output = capsys.readouterr().out
        assert "skipping" in output.lower() or output == ""


class TestCmdSetupVolumes:
    def _mock_client(self, app_id="app-1", app_name="app-abc123"):
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "application.create": {"applicationId": app_id, "appName": app_name},
            "project.create": {
                "project": {"projectId": "proj-1"},
                "environment": {"environmentId": "env-1"},
            },
        }.get(endpoint, {})
        client.get.return_value = []
        return client

    def test_volumes_create_mounts(self, tmp_path):
        """cmd_setup calls mounts.create for each volume."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "volumes": [
                        {"source": "app_data", "target": "/data", "type": "volume"},
                        {"source": "/host/config", "target": "/config", "type": "bind"},
                    ],
                }
            ],
        }
        client = self._mock_client()
        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, cfg, state_file)

        mount_calls = [call for call in client.post.call_args_list if call[0][0] == "mounts.create"]
        assert len(mount_calls) == 2
        assert mount_calls[0][0][1]["type"] == "volume"
        assert mount_calls[0][0][1]["volumeName"] == "app_data"
        assert mount_calls[1][0][1]["type"] == "bind"
        assert mount_calls[1][0][1]["hostPath"] == "/host/config"

    def test_no_volumes_skips_mounts(self, tmp_path):
        """cmd_setup skips mount creation when no volumes defined."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                }
            ],
        }
        client = self._mock_client()
        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, cfg, state_file)

        mount_calls = [call for call in client.post.call_args_list if call[0][0] == "mounts.create"]
        assert len(mount_calls) == 0

    def test_empty_volumes_list_skips_mounts(self, tmp_path):
        """cmd_setup skips mount creation when volumes is an empty list."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "volumes": [],
                }
            ],
        }
        client = self._mock_client()
        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, cfg, state_file)

        mount_calls = [call for call in client.post.call_args_list if call[0][0] == "mounts.create"]
        assert len(mount_calls) == 0


class TestCmdSetupSavesStateOnFailure:
    """cmd_setup saves state after project/app creation so destroy can clean up on later failures."""

    def _mock_client_failing_on(self, fail_endpoint):
        """Client that fails when a specific endpoint is called."""

        def side_effect(endpoint, payload):
            if endpoint == fail_endpoint:
                raise Exception(f"Simulated failure on {endpoint}")
            return {
                "application.create": {"applicationId": "app-1", "appName": "app-abc"},
                "project.create": {
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                },
            }.get(endpoint, {})

        client = MagicMock()
        client.post.side_effect = side_effect
        client.get.return_value = []
        return client

    def test_state_saved_when_domain_creation_fails(self, tmp_path):
        """State file exists after domain.create fails so destroy can clean up."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "domain": {"host": "example.com", "port": 80, "https": False, "certificateType": "none"},
                }
            ],
        }
        client = self._mock_client_failing_on("domain.create")
        state_file = tmp_path / ".dokploy-state" / "test.json"

        with pytest.raises(Exception, match="Simulated failure"):
            dokploy.cmd_setup(client, cfg, state_file)

        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["projectId"] == "proj-1"
        assert "app" in state["apps"]

    def test_state_saved_when_mount_creation_fails(self, tmp_path):
        """State file exists after mounts.create fails so destroy can clean up."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "volumes": [
                        {"source": "data", "target": "/data", "type": "volume"},
                    ],
                }
            ],
        }
        client = self._mock_client_failing_on("mounts.create")
        state_file = tmp_path / ".dokploy-state" / "test.json"

        with pytest.raises(Exception, match="Simulated failure"):
            dokploy.cmd_setup(client, cfg, state_file)

        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["projectId"] == "proj-1"
        assert "app" in state["apps"]

    def test_no_state_saved_when_project_creation_fails(self, tmp_path):
        """No state file when project.create itself fails — nothing to clean up."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                }
            ],
        }
        client = self._mock_client_failing_on("project.create")
        state_file = tmp_path / ".dokploy-state" / "test.json"

        with pytest.raises(Exception, match="Simulated failure"):
            dokploy.cmd_setup(client, cfg, state_file)

        assert not state_file.exists()


def _state_with_app(app_name="web", app_id="app-123", dokploy_name="app-foo-bar-abc"):
    return {
        "projectId": "proj-1",
        "environmentId": "env-1",
        "apps": {
            app_name: {
                "applicationId": app_id,
                "appName": dokploy_name,
            }
        },
    }


CONTAINERS_RESPONSE = [
    {"containerId": "abc123running", "name": "app-foo-bar-abc.1.task1", "state": "running"},
    {"containerId": "def456exited1", "name": "app-foo-bar-abc.1.task2", "state": "exited"},
    {"containerId": "ghi789exited2", "name": "app-foo-bar-abc.1.task3", "state": "exited"},
]


class TestGetContainers:
    def test_returns_containers_from_api(self):
        client = MagicMock()
        client.get.return_value = CONTAINERS_RESPONSE
        result = dokploy.get_containers(client, "app-foo-bar-abc")
        client.get.assert_called_once_with(
            "docker.getContainersByAppNameMatch",
            params={"appName": "app-foo-bar-abc"},
        )
        assert len(result) == 3

    def test_empty_containers(self):
        client = MagicMock()
        client.get.return_value = []
        result = dokploy.get_containers(client, "app-no-exist")
        assert result == []


class TestSelectContainer:
    def test_default_selects_first(self):
        container = dokploy.select_container(CONTAINERS_RESPONSE, exited=False)
        assert container["containerId"] == "abc123running"

    def test_default_selects_first_even_if_exited(self):
        exited_only = [c for c in CONTAINERS_RESPONSE if c["state"] == "exited"]
        container = dokploy.select_container(exited_only, exited=False)
        assert container["containerId"] == "def456exited1"

    def test_for_exec_requires_running(self, capsys):
        exited_only = [c for c in CONTAINERS_RESPONSE if c["state"] == "exited"]
        with pytest.raises(SystemExit):
            dokploy.select_container(exited_only, exited=False, for_exec=True)
        assert "No running container" in capsys.readouterr().out

    def test_for_exec_selects_running(self):
        container = dokploy.select_container(CONTAINERS_RESPONSE, exited=False, for_exec=True)
        assert container["containerId"] == "abc123running"
        assert container["state"] == "running"

    def test_exited_flag_prompts_selection(self):
        with patch("builtins.input", return_value="1"):
            container = dokploy.select_container(CONTAINERS_RESPONSE, exited=True)
        assert container["containerId"] == "abc123running"

    def test_exited_selection_by_index(self):
        with patch("builtins.input", return_value="2"):
            container = dokploy.select_container(CONTAINERS_RESPONSE, exited=True)
        assert container["containerId"] == "def456exited1"

    def test_exited_invalid_input_retries(self, capsys):
        with patch("builtins.input", side_effect=["0", "999", "abc", "2"]):
            container = dokploy.select_container(CONTAINERS_RESPONSE, exited=True)
        assert container["containerId"] == "def456exited1"

    def test_empty_containers_exits(self, capsys):
        with pytest.raises(SystemExit):
            dokploy.select_container([], exited=False)
        assert "No containers found" in capsys.readouterr().out


class TestBuildDockerUrl:
    def test_default_port(self):
        ssh_cfg = {"host": "dokploy.example.com", "user": "root", "port": 22}
        url = dokploy.build_docker_url(ssh_cfg)
        assert url == "ssh://root@dokploy.example.com"

    def test_custom_port(self):
        ssh_cfg = {"host": "dokploy.example.com", "user": "deploy", "port": 2222}
        url = dokploy.build_docker_url(ssh_cfg)
        assert url == "ssh://deploy@dokploy.example.com:2222"

    def test_default_user(self):
        ssh_cfg = {"host": "10.0.0.1"}
        url = dokploy.build_docker_url(ssh_cfg)
        assert url == "ssh://root@10.0.0.1"


class TestGetDockerClient:
    def test_creates_client_with_ssh_url(self):
        ssh_cfg = {"host": "dokploy.example.com", "user": "ubuntu", "port": 22}
        with patch.object(icarus_ssh.docker, "DockerClient") as mock_cls:
            dokploy.get_docker_client(ssh_cfg)
            mock_cls.assert_called_once_with(
                base_url="ssh://ubuntu@dokploy.example.com",
                use_ssh_client=True,
            )


class TestGetSshConfig:
    def test_reads_from_env(self):
        env = {
            "DOKPLOY_SSH_HOST": "myhost.com",
            "DOKPLOY_SSH_USER": "deploy",
            "DOKPLOY_SSH_PORT": "2222",
        }
        with patch.object(
            icarus_ssh,
            "config",
            side_effect=lambda k, **kw: env.get(k, kw.get("default", "")),
        ):
            cfg = dokploy.get_ssh_config()
        assert cfg == {"host": "myhost.com", "user": "deploy", "port": 2222}

    def test_defaults(self):
        env = {"DOKPLOY_SSH_HOST": "myhost.com"}

        def _config(k, **kw):
            return env.get(k, kw.get("default", ""))

        with patch.object(icarus_ssh, "config", side_effect=_config):
            cfg = dokploy.get_ssh_config()
        assert cfg == {"host": "myhost.com", "user": "root", "port": 22}

    def test_missing_host_exits(self, capsys):
        with (
            patch.object(
                icarus_ssh,
                "config",
                side_effect=lambda k, **kw: kw.get("default", ""),
            ),
            pytest.raises(SystemExit),
        ):
            dokploy.get_ssh_config()
        assert "DOKPLOY_SSH_HOST" in capsys.readouterr().out


class TestResolveAppName:
    def test_resolves_known_name(self):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        assert dokploy.resolve_app_name(state, "web") == "web"

    def test_unknown_app_exits(self, capsys):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        with pytest.raises(SystemExit):
            dokploy.resolve_app_name(state, "nonexistent")
        assert "Unknown app" in capsys.readouterr().out

    def test_single_app_auto_selects(self):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        assert dokploy.resolve_app_name(state, None) == "web"

    def test_multiple_apps_no_name_exits(self, capsys):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        state["apps"]["worker"] = {"applicationId": "app-456", "appName": "app-baz-qux-def"}
        with pytest.raises(SystemExit):
            dokploy.resolve_app_name(state, None)
        assert "specify an app" in capsys.readouterr().out


class TestResolveAppForExec:
    def test_resolves_from_state(self):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        app_name = dokploy.resolve_app_for_exec(state, "web")
        assert app_name == "app-foo-bar-abc"

    def test_unknown_app_exits(self, capsys):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        with pytest.raises(SystemExit):
            dokploy.resolve_app_for_exec(state, "nonexistent")
        assert "Unknown app" in capsys.readouterr().out

    def test_single_app_auto_selects(self):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        app_name = dokploy.resolve_app_for_exec(state, None)
        assert app_name == "app-foo-bar-abc"

    def test_multiple_apps_no_name_exits(self, capsys):
        state = _state_with_app("web", "app-123", "app-foo-bar-abc")
        state["apps"]["worker"] = {
            "applicationId": "app-456",
            "appName": "app-baz-qux-def",
        }
        with pytest.raises(SystemExit):
            dokploy.resolve_app_for_exec(state, None)
        assert "specify an app" in capsys.readouterr().out


class TestBuildSchedulePayload:
    def test_minimal_schedule(self):
        """Minimal schedule with only required fields."""
        sched = {
            "name": "daily-run",
            "cronExpression": "0 9 * * *",
            "command": "python run.py",
        }
        result = dokploy.build_schedule_payload("app-1", sched)
        assert result == {
            "name": "daily-run",
            "cronExpression": "0 9 * * *",
            "command": "python run.py",
            "scheduleType": "application",
            "applicationId": "app-1",
            "shellType": "bash",
            "enabled": True,
        }

    def test_all_optional_fields(self):
        """Schedule with all optional fields specified."""
        sched = {
            "name": "weekday-run",
            "cronExpression": "0 9 * * 1-5",
            "command": "python run.py",
            "shellType": "sh",
            "timezone": "America/Chicago",
            "enabled": False,
        }
        result = dokploy.build_schedule_payload("app-1", sched)
        assert result["shellType"] == "sh"
        assert result["timezone"] == "America/Chicago"
        assert result["enabled"] is False

    def test_defaults_shelltype_to_bash(self):
        """shellType defaults to bash when not specified."""
        sched = {
            "name": "job",
            "cronExpression": "* * * * *",
            "command": "echo hi",
        }
        result = dokploy.build_schedule_payload("app-1", sched)
        assert result["shellType"] == "bash"

    def test_defaults_enabled_to_true(self):
        """enabled defaults to True when not specified."""
        sched = {
            "name": "job",
            "cronExpression": "* * * * *",
            "command": "echo hi",
        }
        result = dokploy.build_schedule_payload("app-1", sched)
        assert result["enabled"] is True

    def test_timezone_omitted_when_not_specified(self):
        """timezone key is absent from payload when not in schedule config."""
        sched = {
            "name": "job",
            "cronExpression": "* * * * *",
            "command": "echo hi",
        }
        result = dokploy.build_schedule_payload("app-1", sched)
        assert "timezone" not in result


class TestCmdSetupSchedules:
    def _mock_client(self, app_id="app-1", app_name="app-abc123"):
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "application.create": {"applicationId": app_id, "appName": app_name},
            "project.create": {
                "project": {"projectId": "proj-1"},
                "environment": {"environmentId": "env-1"},
            },
            "schedule.create": {"scheduleId": "sched-001"},
        }.get(endpoint, {})
        client.get.return_value = []
        return client

    def test_schedules_create_calls(self, tmp_path):
        """cmd_setup calls schedule.create for each schedule."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "schedules": [
                        {
                            "name": "weekday-run",
                            "cronExpression": "0 9 * * 1-5",
                            "command": "python run.py",
                            "shellType": "bash",
                            "timezone": "America/Chicago",
                        },
                        {
                            "name": "nightly",
                            "cronExpression": "0 0 * * *",
                            "command": "python cleanup.py",
                        },
                    ],
                }
            ],
        }
        client = self._mock_client()
        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, cfg, state_file)

        sched_calls = [call for call in client.post.call_args_list if call[0][0] == "schedule.create"]
        assert len(sched_calls) == 2
        assert sched_calls[0][0][1]["name"] == "weekday-run"
        assert sched_calls[0][0][1]["cronExpression"] == "0 9 * * 1-5"
        assert sched_calls[0][0][1]["timezone"] == "America/Chicago"
        assert sched_calls[1][0][1]["name"] == "nightly"

    def test_schedules_stored_in_state(self, tmp_path):
        """cmd_setup stores scheduleIds in state."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "schedules": [
                        {
                            "name": "weekday-run",
                            "cronExpression": "0 9 * * 1-5",
                            "command": "python run.py",
                        },
                    ],
                }
            ],
        }
        client = self._mock_client()
        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, cfg, state_file)

        state = json.loads(state_file.read_text())
        assert "schedules" in state["apps"]["app"]
        assert "weekday-run" in state["apps"]["app"]["schedules"]
        assert state["apps"]["app"]["schedules"]["weekday-run"]["scheduleId"] == "sched-001"

    def test_no_schedules_skips(self, tmp_path):
        """cmd_setup skips schedule creation when no schedules defined."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                }
            ],
        }
        client = self._mock_client()
        state_file = tmp_path / ".dokploy-state" / "test.json"
        dokploy.cmd_setup(client, cfg, state_file)

        sched_calls = [call for call in client.post.call_args_list if call[0][0] == "schedule.create"]
        assert len(sched_calls) == 0

    def test_state_saved_when_schedule_creation_fails(self, tmp_path):
        """State file exists after schedule.create fails so destroy can clean up."""

        def side_effect(endpoint, payload):
            if endpoint == "schedule.create":
                raise Exception("Simulated failure on schedule.create")
            return {
                "application.create": {"applicationId": "app-1", "appName": "app-abc"},
                "project.create": {
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                },
            }.get(endpoint, {})

        client = MagicMock()
        client.post.side_effect = side_effect
        client.get.return_value = []

        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "app",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "schedules": [
                        {
                            "name": "job",
                            "cronExpression": "* * * * *",
                            "command": "echo hi",
                        },
                    ],
                }
            ],
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"

        with pytest.raises(Exception, match="Simulated failure"):
            dokploy.cmd_setup(client, cfg, state_file)

        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["projectId"] == "proj-1"
        assert "app" in state["apps"]


class TestScheduleReconciliation:
    def test_reconcile_creates_new_deletes_removed_updates_existing(self):
        """reconcile_schedules creates new, updates existing, deletes removed."""
        existing = [
            {
                "scheduleId": "s1",
                "name": "keep-me",
                "cronExpression": "0 0 * * *",
                "command": "old cmd",
                "shellType": "bash",
                "enabled": True,
            },
            {
                "scheduleId": "s2",
                "name": "remove-me",
                "cronExpression": "0 0 * * *",
                "command": "bye",
                "shellType": "bash",
                "enabled": True,
            },
        ]
        desired = [
            {"name": "keep-me", "cronExpression": "0 9 * * *", "command": "new cmd"},
            {"name": "add-me", "cronExpression": "0 12 * * *", "command": "hello"},
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "schedule.create": {"scheduleId": "s3"},
        }.get(endpoint, {})

        result = dokploy.reconcile_schedules(client, "app-1", existing, desired)

        # Check update call for "keep-me"
        update_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["scheduleId"] == "s1"
        assert update_calls[0][0][1]["cronExpression"] == "0 9 * * *"
        assert update_calls[0][0][1]["command"] == "new cmd"

        # Check delete call for "remove-me"
        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["scheduleId"] == "s2"

        # Check create call for "add-me"
        create_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["name"] == "add-me"

        # Check returned state
        assert "keep-me" in result
        assert result["keep-me"]["scheduleId"] == "s1"
        assert "add-me" in result
        assert result["add-me"]["scheduleId"] == "s3"
        assert "remove-me" not in result

    def test_reconcile_no_changes(self):
        """reconcile_schedules does nothing when desired matches existing."""
        existing = [
            {
                "scheduleId": "s1",
                "name": "job",
                "cronExpression": "0 0 * * *",
                "command": "echo hi",
                "shellType": "bash",
                "enabled": True,
            },
        ]
        desired = [
            {"name": "job", "cronExpression": "0 0 * * *", "command": "echo hi"},
        ]
        client = MagicMock()
        dokploy.reconcile_schedules(client, "app-1", existing, desired)

        # No update needed since cron+command+defaults match
        update_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.update"]
        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.delete"]
        create_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.create"]
        assert len(update_calls) == 0
        assert len(delete_calls) == 0
        assert len(create_calls) == 0


class TestScheduleSchemaValidation:
    def test_schedules_config_valid(self, schedules_config):
        dokploy.validate_config(schedules_config)

    def test_schedules_parsed_correctly(self, schedules_config):
        app = next(a for a in schedules_config["apps"] if a["name"] == "web")
        assert len(app["schedules"]) == 2
        assert app["schedules"][0]["name"] == "weekday-run"
        assert app["schedules"][0]["cronExpression"] == "0 9 * * 1-5"
        assert app["schedules"][0]["shellType"] == "bash"
        assert app["schedules"][0]["timezone"] == "America/Chicago"
        assert app["schedules"][1]["name"] == "nightly-cleanup"
        assert app["schedules"][1]["cronExpression"] == "0 0 * * *"

    def test_env_override_schedules(self):
        """Environment overrides can override schedules per-app."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "schedules": [
                        {"name": "job", "cronExpression": "0 0 * * *", "command": "echo base"},
                    ],
                }
            ],
            "environments": {
                "prod": {
                    "apps": {
                        "web": {
                            "schedules": [
                                {"name": "job", "cronExpression": "0 9 * * 1-5", "command": "echo prod"},
                            ],
                        }
                    }
                }
            },
        }
        merged = dokploy.merge_env_overrides(cfg, "prod")
        web = next(a for a in merged["apps"] if a["name"] == "web")
        assert web["schedules"][0]["cronExpression"] == "0 9 * * 1-5"
        assert web["schedules"][0]["command"] == "echo prod"


class TestCmdClean:
    def test_cmd_clean_calls_cleanup_stale_routes(self, tmp_path, monkeypatch):
        """cmd_clean delegates to cleanup_stale_routes with state and config."""
        calls = []
        state = {"projectId": "proj-1", "apps": {"web": {"appName": "app-web-abc"}}}
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda s, c: calls.append(("cleanup", s, c)))

        cfg = {"apps": [{"name": "web"}]}
        dokploy.cmd_clean(cfg, state_file)

        assert len(calls) == 1
        assert calls[0][0] == "cleanup"
        assert calls[0][1]["apps"]["web"]["appName"] == "app-web-abc"
        assert calls[0][2] is cfg


class TestCmdDestroyCallsCleanup:
    def test_destroy_calls_cleanup_before_delete(self, tmp_path, monkeypatch):
        """cmd_destroy calls cleanup_stale_routes before deleting the project."""
        order = []
        state = {"projectId": "proj-1", "apps": {"web": {"appName": "app-web-abc"}}}
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda s, c: order.append("cleanup"))

        client = MagicMock()
        client.post = MagicMock(side_effect=lambda *a, **kw: order.append("project.remove"))

        cfg = {"apps": [{"name": "web"}]}
        dokploy.cmd_destroy(client, cfg, state_file)

        assert order == ["cleanup", "project.remove"]
        assert not state_file.exists()


class TestCleanupSkipsWhenAllSshVarsMissing:
    def test_skips_when_all_three_ssh_vars_empty(self, monkeypatch, capsys):
        """cleanup_stale_routes skips when DOKPLOY_SSH_HOST, _USER, and _PORT are all empty."""
        monkeypatch.setattr(icarus_ssh, "config", MagicMock(side_effect=lambda key, default="": default))

        state = {"apps": {"web": {"appName": "app-web-abc"}}}
        cfg = {
            "apps": [
                {"name": "web", "domain": {"host": "example.com", "port": 80, "https": True, "certificateType": "letsencrypt"}}
            ]
        }

        dokploy.cleanup_stale_routes(state, cfg)

        output = capsys.readouterr().out
        assert "skipping" in output.lower()

    def test_proceeds_when_only_host_set(self, monkeypatch):
        """cleanup_stale_routes proceeds (does not skip) when at least DOKPLOY_SSH_HOST is set."""

        def fake_config(key, default=""):
            if key == "DOKPLOY_SSH_HOST":
                return "server.example.com"
            return default

        monkeypatch.setattr(icarus_ssh, "config", MagicMock(side_effect=fake_config))

        ssh_mock = MagicMock()
        ssh_mock.exec_command.return_value = (None, MagicMock(read=lambda: b""), None)
        ssh_class = MagicMock(return_value=ssh_mock)
        monkeypatch.setattr(icarus_ssh.paramiko, "SSHClient", ssh_class)

        state = {"apps": {"web": {"appName": "app-web-abc"}}}
        cfg = {
            "apps": [
                {"name": "web", "domain": {"host": "example.com", "port": 80, "https": True, "certificateType": "letsencrypt"}}
            ]
        }

        dokploy.cleanup_stale_routes(state, cfg)

        ssh_mock.connect.assert_called_once()


def _make_plan_state(app_defs, project_id="proj-123", environment_id="env-456"):
    """Build a minimal state dict from app definitions for plan tests."""
    state = {
        "projectId": project_id,
        "environmentId": environment_id,
        "apps": {},
    }
    for app_def in app_defs:
        name = app_def["name"]
        if dokploy.is_compose(app_def):
            state["apps"][name] = {
                "composeId": f"compose-{name}-001",
                "appName": f"{name}-abc123",
                "source": "compose",
            }
        else:
            state["apps"][name] = {
                "applicationId": f"app-{name}-001",
                "appName": f"{name}-abc123",
            }
    return state


class TestComputePlanInitialSetup:
    """When no state file exists, compute_plan shows all resources as creates."""

    def test_shows_project_create(self, tmp_path, minimal_config):
        cfg = dokploy.merge_env_overrides(minimal_config, "dev")
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        client = MagicMock()

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        project_changes = [c for c in changes if c["resource_type"] == "project"]
        assert len(project_changes) == 1
        assert project_changes[0]["action"] == "create"
        assert project_changes[0]["attrs"]["name"] == "my-app"

    def test_shows_app_creates(self, tmp_path, minimal_config):
        cfg = dokploy.merge_env_overrides(minimal_config, "dev")
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        client = MagicMock()

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        app_changes = [c for c in changes if c["resource_type"] == "application"]
        assert len(app_changes) == 1
        assert app_changes[0]["action"] == "create"
        assert app_changes[0]["name"] == "app"
        assert app_changes[0]["attrs"]["source"] == "docker"
        assert app_changes[0]["attrs"]["dockerImage"] == "nginx:alpine"

    def test_shows_domain_creates(self, tmp_path, minimal_config):
        cfg = dokploy.merge_env_overrides(minimal_config, "prod")
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        client = MagicMock()

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        domain_changes = [c for c in changes if c["resource_type"] == "domain"]
        assert len(domain_changes) == 1
        assert domain_changes[0]["action"] == "create"
        assert domain_changes[0]["attrs"]["host"] == "app.example.com"

    def test_shows_env_creates_for_env_targets(self, tmp_path, web_app_config):
        cfg = dokploy.merge_env_overrides(web_app_config, "dev")
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        (tmp_path / ".env").write_text("APP_SECRET=hunter2\nDATABASE_URL=postgres://localhost/db\n")
        client = MagicMock()

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        env_changes = [c for c in changes if c["resource_type"] == "environment"]
        target_names = {c["name"] for c in env_changes}
        assert "web" in target_names
        assert "worker" in target_names

    def test_shows_schedule_creates(self, tmp_path, schedules_config):
        cfg = dokploy.merge_env_overrides(schedules_config, "dev")
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        client = MagicMock()

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        sched_changes = [c for c in changes if c["resource_type"] == "schedule"]
        assert len(sched_changes) == 2
        assert all(c["action"] == "create" for c in sched_changes)
        names = {c["name"] for c in sched_changes}
        assert names == {"weekday-run", "nightly-cleanup"}

    def test_shows_volume_creates(self, tmp_path, volumes_config):
        cfg = dokploy.merge_env_overrides(volumes_config, "dev")
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        client = MagicMock()

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        mount_changes = [c for c in changes if c["resource_type"] == "mount"]
        assert len(mount_changes) > 0
        assert all(c["action"] == "create" for c in mount_changes)

    def test_no_api_calls_when_no_state(self, tmp_path, minimal_config):
        cfg = dokploy.merge_env_overrides(minimal_config, "dev")
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        client = MagicMock()

        dokploy.compute_plan(client, cfg, state_file, tmp_path)

        client.get.assert_not_called()
        client.post.assert_not_called()


class TestComputePlanNoChanges:
    """When state exists and remote matches config, compute_plan returns empty."""

    def test_env_up_to_date(self, tmp_path, minimal_config):
        cfg = dokploy.merge_env_overrides(minimal_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-123"}]
            if endpoint == "application.one":
                return {
                    "applicationId": "app-app-001",
                    "appName": "app-abc123",
                    "env": None,
                }
            if endpoint == "schedule.list":
                return []
            return {}

        client = MagicMock()
        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        assert len(changes) == 0

    def test_schedules_up_to_date(self, tmp_path, schedules_config):
        cfg = dokploy.merge_env_overrides(schedules_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-123"}]
            if endpoint == "application.one":
                return {
                    "applicationId": params["applicationId"],
                    "env": None,
                }
            if endpoint == "schedule.list":
                return [
                    {
                        "scheduleId": "sched-1",
                        "name": "weekday-run",
                        "cronExpression": "0 9 * * 1-5",
                        "command": "python run.py",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": "America/Chicago",
                    },
                    {
                        "scheduleId": "sched-2",
                        "name": "nightly-cleanup",
                        "cronExpression": "0 0 * * *",
                        "command": "python cleanup.py",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": None,
                    },
                ]
            return {}

        client = MagicMock()
        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        assert len(changes) == 0


class TestComputePlanEnvChanges:
    """When env vars differ between local and remote, plan shows updates."""

    def test_env_target_keys_changed(self, tmp_path, web_app_config):
        cfg = dokploy.merge_env_overrides(web_app_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))
        (tmp_path / ".env").write_text("APP_SECRET=hunter2\nNEW_VAR=new\n")

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-123"}]
            if endpoint == "application.one":
                return {
                    "applicationId": params["applicationId"],
                    "env": "APP_SECRET=oldvalue\nOLD_VAR=stale\n",
                }
            if endpoint == "compose.one":
                return {"composeId": params["composeId"], "env": ""}
            if endpoint == "schedule.list":
                return []
            return {}

        client = MagicMock()
        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        env_changes = [c for c in changes if c["resource_type"] == "environment"]
        assert len(env_changes) > 0
        assert all(c["action"] == "update" for c in env_changes)

    def test_custom_env_changed(self, tmp_path, docker_only_config):
        cfg = dokploy.merge_env_overrides(docker_only_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-123"}]
            if endpoint == "application.one":
                app_id = params["applicationId"]
                if "app" in app_id:
                    return {
                        "applicationId": app_id,
                        "env": "DATABASE_URL=old\n",
                    }
                return {"applicationId": app_id, "env": None}
            if endpoint == "schedule.list":
                return []
            return {}

        client = MagicMock()
        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        env_changes = [c for c in changes if c["resource_type"] == "environment"]
        app_env = [c for c in env_changes if c["name"] == "app"]
        assert len(app_env) == 1
        assert app_env[0]["action"] == "update"
        assert "added" in app_env[0]["attrs"]
        assert "removed" in app_env[0]["attrs"]


class TestComputePlanScheduleChanges:
    """When schedules differ, plan shows creates/updates/deletes."""

    def test_schedule_added(self, tmp_path, schedules_config):
        cfg = dokploy.merge_env_overrides(schedules_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-123"}]
            if endpoint == "application.one":
                return {
                    "applicationId": params["applicationId"],
                    "env": None,
                }
            if endpoint == "schedule.list":
                return [
                    {
                        "scheduleId": "sched-1",
                        "name": "weekday-run",
                        "cronExpression": "0 9 * * 1-5",
                        "command": "python run.py",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": "America/Chicago",
                    },
                ]
            return {}

        client = MagicMock()
        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        sched_creates = [c for c in changes if c["resource_type"] == "schedule" and c["action"] == "create"]
        assert len(sched_creates) == 1
        assert sched_creates[0]["name"] == "nightly-cleanup"

    def test_schedule_updated(self, tmp_path, schedules_config):
        cfg = dokploy.merge_env_overrides(schedules_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-123"}]
            if endpoint == "application.one":
                return {
                    "applicationId": params["applicationId"],
                    "env": None,
                }
            if endpoint == "schedule.list":
                return [
                    {
                        "scheduleId": "sched-1",
                        "name": "weekday-run",
                        "cronExpression": "0 0 * * *",
                        "command": "python run.py",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": "America/Chicago",
                    },
                    {
                        "scheduleId": "sched-2",
                        "name": "nightly-cleanup",
                        "cronExpression": "0 0 * * *",
                        "command": "python cleanup.py",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": None,
                    },
                ]
            return {}

        client = MagicMock()
        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        sched_updates = [c for c in changes if c["resource_type"] == "schedule" and c["action"] == "update"]
        assert len(sched_updates) == 1
        assert sched_updates[0]["name"] == "weekday-run"
        assert sched_updates[0]["attrs"]["cronExpression"] == (
            "0 0 * * *",
            "0 9 * * 1-5",
        )

    def test_schedule_removed(self, tmp_path, schedules_config):
        cfg = dokploy.merge_env_overrides(schedules_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-123"}]
            if endpoint == "application.one":
                return {
                    "applicationId": params["applicationId"],
                    "env": None,
                }
            if endpoint == "schedule.list":
                return [
                    {
                        "scheduleId": "sched-1",
                        "name": "weekday-run",
                        "cronExpression": "0 9 * * 1-5",
                        "command": "python run.py",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": "America/Chicago",
                    },
                    {
                        "scheduleId": "sched-2",
                        "name": "nightly-cleanup",
                        "cronExpression": "0 0 * * *",
                        "command": "python cleanup.py",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": None,
                    },
                    {
                        "scheduleId": "sched-3",
                        "name": "old-job",
                        "cronExpression": "0 0 * * *",
                        "command": "echo stale",
                        "shellType": "bash",
                        "enabled": True,
                        "timezone": None,
                    },
                ]
            return {}

        client = MagicMock()
        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        sched_deletes = [c for c in changes if c["resource_type"] == "schedule" and c["action"] == "destroy"]
        assert len(sched_deletes) == 1
        assert sched_deletes[0]["name"] == "old-job"


class TestPrintPlan:
    """Test the formatted output of print_plan."""

    def test_no_changes_message(self, capsys):
        dokploy.print_plan([])
        captured = capsys.readouterr()
        assert "up to date" in captured.out.lower()

    def test_summary_line(self, capsys):
        changes = [
            {
                "action": "create",
                "resource_type": "project",
                "name": "my-app",
                "parent": None,
                "attrs": {"name": "my-app"},
            },
            {
                "action": "create",
                "resource_type": "application",
                "name": "web",
                "parent": None,
                "attrs": {"source": "docker"},
            },
            {
                "action": "update",
                "resource_type": "environment",
                "name": "web",
                "parent": None,
                "attrs": {"added": ["NEW"], "removed": ["OLD"]},
            },
            {
                "action": "destroy",
                "resource_type": "schedule",
                "name": "old-job",
                "parent": "web",
                "attrs": {"command": "echo old"},
            },
        ]
        dokploy.print_plan(changes)
        captured = capsys.readouterr()
        assert "2 to create" in captured.out
        assert "1 to update" in captured.out
        assert "1 to destroy" in captured.out

    def test_create_uses_plus_symbol(self, capsys):
        changes = [
            {
                "action": "create",
                "resource_type": "application",
                "name": "web",
                "parent": None,
                "attrs": {"source": "docker"},
            },
        ]
        dokploy.print_plan(changes)
        captured = capsys.readouterr()
        assert "+ " in captured.out

    def test_update_uses_tilde_symbol(self, capsys):
        changes = [
            {
                "action": "update",
                "resource_type": "environment",
                "name": "web",
                "parent": None,
                "attrs": {"added": ["NEW"], "removed": []},
            },
        ]
        dokploy.print_plan(changes)
        captured = capsys.readouterr()
        assert "~ " in captured.out

    def test_destroy_uses_minus_symbol(self, capsys):
        changes = [
            {
                "action": "destroy",
                "resource_type": "schedule",
                "name": "old-job",
                "parent": "web",
                "attrs": {"command": "echo old"},
            },
        ]
        dokploy.print_plan(changes)
        captured = capsys.readouterr()
        assert "- " in captured.out


class TestComputePlanOrphanedState:
    """When state exists but project is gone, plan shows full creates."""

    def test_orphaned_state_shows_creates(self, tmp_path, minimal_config):
        cfg = dokploy.merge_env_overrides(minimal_config, "dev")
        state = _make_plan_state(cfg["apps"])
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(state))

        client = MagicMock()
        client.get.side_effect = lambda endpoint, params=None: [] if endpoint == "project.all" else {}

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        project_changes = [c for c in changes if c["resource_type"] == "project"]
        assert len(project_changes) == 1
        assert project_changes[0]["action"] == "create"


class TestCmdPlan:
    """Test cmd_plan calls compute_plan and print_plan."""

    def test_cmd_plan_runs(self, tmp_path, minimal_config, capsys):
        cfg = dokploy.merge_env_overrides(minimal_config, "dev")
        state_file = tmp_path / ".dokploy-state" / "dev.json"
        client = MagicMock()

        dokploy.cmd_plan(client, cfg, state_file, tmp_path)

        captured = capsys.readouterr()
        assert "create" in captured.out.lower()


class TestDomainReconciliation:
    def test_reconcile_creates_new_deletes_removed_updates_existing(self):
        """reconcile_domains creates new, updates changed, deletes removed."""
        existing = [
            {
                "domainId": "d1",
                "host": "keep.example.com",
                "port": 3000,
                "https": True,
                "certificateType": "letsencrypt",
            },
            {
                "domainId": "d2",
                "host": "remove.example.com",
                "port": 8080,
                "https": False,
                "certificateType": "none",
            },
        ]
        desired = [
            {
                "host": "keep.example.com",
                "port": 4000,
                "https": True,
                "certificateType": "letsencrypt",
            },
            {
                "host": "add.example.com",
                "port": 9090,
                "https": True,
                "certificateType": "letsencrypt",
            },
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "domain.create": {"domainId": "d3"},
        }.get(endpoint, {})

        result = dokploy.reconcile_domains(client, "app-1", existing, desired, compose=False)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["domainId"] == "d1"
        assert update_calls[0][0][1]["port"] == 4000

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["domainId"] == "d2"

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["host"] == "add.example.com"

        assert "keep.example.com" in result
        assert result["keep.example.com"]["domainId"] == "d1"
        assert "add.example.com" in result
        assert result["add.example.com"]["domainId"] == "d3"
        assert "remove.example.com" not in result

    def test_reconcile_no_changes(self):
        """reconcile_domains does nothing when desired matches existing."""
        existing = [
            {
                "domainId": "d1",
                "host": "app.example.com",
                "port": 3000,
                "https": True,
                "certificateType": "letsencrypt",
            },
        ]
        desired = [
            {
                "host": "app.example.com",
                "port": 3000,
                "https": True,
                "certificateType": "letsencrypt",
            },
        ]
        client = MagicMock()
        dokploy.reconcile_domains(client, "app-1", existing, desired, compose=False)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.update"]
        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.delete"]
        create_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.create"]
        assert len(update_calls) == 0
        assert len(delete_calls) == 0
        assert len(create_calls) == 0

    def test_reconcile_all_removed(self):
        """reconcile_domains deletes all when desired is empty."""
        existing = [
            {
                "domainId": "d1",
                "host": "app.example.com",
                "port": 3000,
                "https": True,
                "certificateType": "letsencrypt",
            },
        ]
        client = MagicMock()
        result = dokploy.reconcile_domains(client, "app-1", existing, [], compose=False)

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["domainId"] == "d1"
        assert result == {}

    def test_reconcile_compose_domain(self):
        """reconcile_domains passes compose=True through to build_domain_payload."""
        existing = []
        desired = [
            {
                "host": "compose.example.com",
                "port": 3000,
                "https": True,
                "certificateType": "letsencrypt",
                "serviceName": "web",
            },
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "domain.create": {"domainId": "d1"},
        }.get(endpoint, {})

        result = dokploy.reconcile_domains(client, "comp-1", existing, desired, compose=True)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["composeId"] == "comp-1"
        assert create_calls[0][0][1]["serviceName"] == "web"
        assert result["compose.example.com"]["domainId"] == "d1"

    def test_same_host_different_path_creates_both(self):
        """reconcile_domains treats same host + different path as distinct domains."""
        existing = []
        desired = [
            {
                "host": "s3.example.com",
                "port": 9000,
                "path": "/",
                "https": True,
                "certificateType": "letsencrypt",
                "serviceName": "rustfs",
            },
            {
                "host": "s3.example.com",
                "port": 9001,
                "path": "/rustfs/console",
                "https": True,
                "certificateType": "letsencrypt",
                "serviceName": "rustfs",
            },
        ]
        client = MagicMock()
        call_count = {"n": 0}

        def post_side_effect(endpoint, payload):
            if endpoint == "domain.create":
                call_count["n"] += 1
                return {"domainId": f"d{call_count['n']}"}
            return {}

        client.post.side_effect = post_side_effect

        result = dokploy.reconcile_domains(client, "comp-1", existing, desired, compose=True)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.create"]
        assert len(create_calls) == 2
        assert "s3.example.com" in result
        assert "s3.example.com/rustfs/console" in result

    def test_same_host_different_path_updates_only_changed(self):
        """reconcile_domains updates only the path whose port changed, leaves other intact."""
        existing = [
            {
                "domainId": "d1",
                "host": "s3.example.com",
                "port": 9000,
                "path": "/",
                "https": True,
                "certificateType": "letsencrypt",
                "serviceName": "rustfs",
            },
            {
                "domainId": "d2",
                "host": "s3.example.com",
                "port": 9001,
                "path": "/rustfs/console",
                "https": True,
                "certificateType": "letsencrypt",
                "serviceName": "rustfs",
            },
        ]
        desired = [
            {
                "host": "s3.example.com",
                "port": 9000,
                "path": "/",
                "https": True,
                "certificateType": "letsencrypt",
                "serviceName": "rustfs",
            },
            {
                "host": "s3.example.com",
                "port": 9099,  # changed
                "path": "/rustfs/console",
                "https": True,
                "certificateType": "letsencrypt",
                "serviceName": "rustfs",
            },
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {}

        dokploy.reconcile_domains(client, "comp-1", existing, desired, compose=True)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.create"]
        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.delete"]
        update_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.update"]
        assert len(create_calls) == 0
        assert len(delete_calls) == 0
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["domainId"] == "d2"
        assert update_calls[0][0][1]["port"] == 9099


class TestPlanDomainChanges:
    def test_plan_redeploy_shows_domain_changes(self, tmp_path):
        """_plan_redeploy detects domain additions, removals, and updates."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": {"type": "docker", "image": "nginx"},
                    "domain": [
                        {
                            "host": "new.example.com",
                            "port": 3000,
                            "https": True,
                            "certificateType": "letsencrypt",
                        },
                        {
                            "host": "kept.example.com",
                            "port": 4000,
                            "https": True,
                            "certificateType": "letsencrypt",
                        },
                    ],
                },
            ],
        }
        remote_domains = [
            {
                "domainId": "d1",
                "host": "removed.example.com",
                "port": 8080,
                "https": False,
                "certificateType": "none",
            },
            {
                "domainId": "d2",
                "host": "kept.example.com",
                "port": 3000,
                "https": True,
                "certificateType": "letsencrypt",
            },
        ]

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {"env": ""}
            if endpoint == "domain.byApplicationId":
                return remote_domains
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        domain_changes = [c for c in changes if c["resource_type"] == "domain"]
        actions = {c["action"] for c in domain_changes}
        assert "create" in actions
        assert "destroy" in actions
        assert "update" in actions

        created = [c for c in domain_changes if c["action"] == "create"]
        assert len(created) == 1
        assert created[0]["name"] == "new.example.com"

        destroyed = [c for c in domain_changes if c["action"] == "destroy"]
        assert len(destroyed) == 1
        assert destroyed[0]["name"] == "removed.example.com"

        updated = [c for c in domain_changes if c["action"] == "update"]
        assert len(updated) == 1
        assert updated[0]["name"] == "kept.example.com"

    def test_plan_same_host_different_path_creates_missing_only(self, tmp_path):
        """_plan_redeploy treats same host + different path as distinct domains.

        One entry already exists (API at /), the other is new (console at /rustfs/console).
        Exactly one create is emitted and the existing entry is not destroyed.
        """
        state = {
            "projectId": "proj-1",
            "apps": {
                "rustfs": {
                    "composeId": "comp-1",
                    "source": "compose",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "rustfs",
                    "source": "compose",
                    "composeFile": "docker-compose.yml",
                    "composeType": "docker-compose",
                    "domain": [
                        {
                            "host": "s3.example.com",
                            "port": 9000,
                            "path": "/",
                            "https": True,
                            "certificateType": "letsencrypt",
                            "serviceName": "rustfs",
                        },
                        {
                            "host": "s3.example.com",
                            "port": 9001,
                            "path": "/rustfs/console",
                            "https": True,
                            "certificateType": "letsencrypt",
                            "serviceName": "rustfs",
                        },
                    ],
                },
            ],
        }
        remote_domains = [
            {
                "domainId": "d1",
                "host": "s3.example.com",
                "port": 9000,
                "path": "/",
                "https": True,
                "certificateType": "letsencrypt",
            },
        ]

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "compose.one":
                return {"env": "", "composeFile": ""}
            if endpoint == "domain.byComposeId":
                return remote_domains
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        domain_changes = [c for c in changes if c["resource_type"] == "domain"]
        created = [c for c in domain_changes if c["action"] == "create"]
        destroyed = [c for c in domain_changes if c["action"] == "destroy"]
        assert len(created) == 1
        assert "/rustfs/console" in created[0]["name"]
        assert len(destroyed) == 0


class TestMountReconciliation:
    def test_reconcile_creates_new_deletes_removed_updates_existing(self):
        """reconcile_mounts creates new, updates changed, deletes removed."""
        existing = [
            {
                "mountId": "m1",
                "type": "volume",
                "mountPath": "/data",
                "volumeName": "old-vol",
            },
            {
                "mountId": "m2",
                "type": "bind",
                "mountPath": "/logs",
                "hostPath": "/host/logs",
            },
        ]
        desired = [
            {"type": "volume", "target": "/data", "source": "new-vol"},
            {"type": "bind", "target": "/config", "source": "/host/config"},
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "mounts.create": {"mountId": "m3"},
        }.get(endpoint, {})

        result = dokploy.reconcile_mounts(client, "app-1", existing, desired)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["mountId"] == "m1"
        assert update_calls[0][0][1]["volumeName"] == "new-vol"

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.remove"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["mountId"] == "m2"

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["mountPath"] == "/config"

        assert "/data" in result
        assert result["/data"]["mountId"] == "m1"
        assert "/config" in result
        assert result["/config"]["mountId"] == "m3"
        assert "/logs" not in result

    def test_reconcile_no_changes(self):
        """reconcile_mounts does nothing when desired matches existing."""
        existing = [
            {
                "mountId": "m1",
                "type": "volume",
                "mountPath": "/data",
                "volumeName": "my-vol",
            },
        ]
        desired = [
            {"type": "volume", "target": "/data", "source": "my-vol"},
        ]
        client = MagicMock()
        dokploy.reconcile_mounts(client, "app-1", existing, desired)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.update"]
        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.remove"]
        create_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.create"]
        assert len(update_calls) == 0
        assert len(delete_calls) == 0
        assert len(create_calls) == 0

    def test_reconcile_all_removed(self):
        """reconcile_mounts deletes all when desired is empty."""
        existing = [
            {
                "mountId": "m1",
                "type": "volume",
                "mountPath": "/data",
                "volumeName": "my-vol",
            },
        ]
        client = MagicMock()
        result = dokploy.reconcile_mounts(client, "app-1", existing, [])

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.remove"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["mountId"] == "m1"
        assert result == {}

    def test_reconcile_bind_mount_update(self):
        """reconcile_mounts detects hostPath change for bind mounts."""
        existing = [
            {
                "mountId": "m1",
                "type": "bind",
                "mountPath": "/app/data",
                "hostPath": "/old/path",
            },
        ]
        desired = [
            {"type": "bind", "target": "/app/data", "source": "/new/path"},
        ]
        client = MagicMock()
        dokploy.reconcile_mounts(client, "app-1", existing, desired)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["hostPath"] == "/new/path"


class TestPlanMountChanges:
    def test_plan_redeploy_shows_mount_changes(self, tmp_path):
        """_plan_redeploy detects mount additions, removals, and updates."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": {"type": "docker", "image": "nginx"},
                    "volumes": [
                        {"type": "volume", "target": "/data", "source": "new-vol"},
                        {"type": "bind", "target": "/config", "source": "/host/config"},
                    ],
                },
            ],
        }
        remote_mounts = [
            {
                "mountId": "m1",
                "type": "volume",
                "mountPath": "/data",
                "volumeName": "old-vol",
            },
            {
                "mountId": "m2",
                "type": "bind",
                "mountPath": "/logs",
                "hostPath": "/host/logs",
            },
        ]

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {"env": "", "mounts": remote_mounts}
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        mount_changes = [c for c in changes if c["resource_type"] == "mount"]
        actions = {c["action"] for c in mount_changes}
        assert "create" in actions
        assert "destroy" in actions
        assert "update" in actions

        created = [c for c in mount_changes if c["action"] == "create"]
        assert len(created) == 1
        assert created[0]["name"] == "/host/config -> /config"

        destroyed = [c for c in mount_changes if c["action"] == "destroy"]
        assert len(destroyed) == 1
        assert destroyed[0]["name"] == "/host/logs -> /logs"

        updated = [c for c in mount_changes if c["action"] == "update"]
        assert len(updated) == 1
        assert updated[0]["name"] == "new-vol -> /data"


class TestReconcileAppMounts:
    def test_fetches_mounts_from_application_one(self, tmp_path):
        """reconcile_app_mounts reads mounts from application.one, not a separate endpoint."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {"applicationId": "app-1"},
            },
        }
        dokploy.save_state(state, state_file)

        cfg = {
            "apps": [
                {
                    "name": "web",
                    "source": {"type": "docker", "image": "nginx"},
                    "volumes": [
                        {"type": "volume", "target": "/data", "source": "my-vol"},
                    ],
                },
            ],
        }

        client = MagicMock()
        client.get.return_value = {
            "mounts": [
                {
                    "mountId": "m1",
                    "type": "volume",
                    "mountPath": "/data",
                    "volumeName": "my-vol",
                },
            ],
        }

        dokploy.reconcile_app_mounts(client, cfg, state, state_file)

        client.get.assert_called_once_with("application.one", {"applicationId": "app-1"})

    def test_creates_and_deletes_mounts(self, tmp_path):
        """reconcile_app_mounts creates new mounts and removes stale ones."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {"applicationId": "app-1"},
            },
        }
        dokploy.save_state(state, state_file)

        cfg = {
            "apps": [
                {
                    "name": "web",
                    "source": {"type": "docker", "image": "nginx"},
                    "volumes": [
                        {"type": "volume", "target": "/new", "source": "new-vol"},
                    ],
                },
            ],
        }

        client = MagicMock()
        client.get.return_value = {
            "mounts": [
                {
                    "mountId": "m1",
                    "type": "bind",
                    "mountPath": "/old",
                    "hostPath": "/host/old",
                },
            ],
        }
        client.post.side_effect = lambda endpoint, payload: {
            "mounts.create": {"mountId": "m2"},
        }.get(endpoint, {})

        dokploy.reconcile_app_mounts(client, cfg, state, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["mountPath"] == "/new"

        remove_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.remove"]
        assert len(remove_calls) == 1
        assert remove_calls[0][0][1]["mountId"] == "m1"

        saved = dokploy.load_state(state_file)
        assert "/new" in saved["apps"]["web"]["mounts"]
        assert saved["apps"]["web"]["mounts"]["/new"]["mountId"] == "m2"

    def test_skips_compose_apps(self, tmp_path):
        """reconcile_app_mounts skips compose apps (mounts are app-only)."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {
                "stack": {"composeId": "comp-1", "source": "compose"},
            },
        }
        dokploy.save_state(state, state_file)

        cfg = {
            "apps": [
                {
                    "name": "stack",
                    "source": "compose",
                    "volumes": [
                        {"type": "volume", "target": "/data", "source": "vol"},
                    ],
                },
            ],
        }

        client = MagicMock()
        dokploy.reconcile_app_mounts(client, cfg, state, state_file)

        client.get.assert_not_called()
        client.post.assert_not_called()

    def test_removes_all_when_volumes_cleared(self, tmp_path):
        """reconcile_app_mounts removes all mounts when volumes removed from config."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                    "mounts": {"/data": {"mountId": "m1"}},
                },
            },
        }
        dokploy.save_state(state, state_file)

        cfg = {
            "apps": [
                {
                    "name": "web",
                    "source": {"type": "docker", "image": "nginx"},
                },
            ],
        }

        client = MagicMock()
        client.get.return_value = {
            "mounts": [
                {
                    "mountId": "m1",
                    "type": "volume",
                    "mountPath": "/data",
                    "volumeName": "my-vol",
                },
            ],
        }

        dokploy.reconcile_app_mounts(client, cfg, state, state_file)

        remove_calls = [c for c in client.post.call_args_list if c[0][0] == "mounts.remove"]
        assert len(remove_calls) == 1
        assert remove_calls[0][0][1]["mountId"] == "m1"

        saved = dokploy.load_state(state_file)
        assert saved["apps"]["web"]["mounts"] == {}


class TestCmdApplyCallsReconcileMounts:
    def test_redeploy_calls_reconcile_app_mounts(self, tmp_path, monkeypatch):
        """cmd_apply calls reconcile_app_mounts during redeploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("{}")

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)
        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda state, cfg: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_domains", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_schedules", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_mounts",
            lambda client, cfg, state, sf: calls.append("reconcile_app_mounts"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_mounts" in calls

    def test_fresh_deploy_does_not_call_reconcile_app_mounts(self, tmp_path, monkeypatch):
        """cmd_apply does NOT call reconcile_app_mounts on fresh deploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_mounts",
            lambda client, cfg, state, sf: calls.append("reconcile_app_mounts"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_mounts" not in calls


class TestBuildPortPayload:
    def test_minimal_port(self):
        """build_port_payload with only required fields."""
        result = dokploy.build_port_payload(
            "app-1",
            {
                "publishedPort": 8080,
                "targetPort": 80,
            },
        )
        assert result == {
            "applicationId": "app-1",
            "publishedPort": 8080,
            "targetPort": 80,
            "protocol": "tcp",
            "publishMode": "ingress",
        }

    def test_full_port(self):
        """build_port_payload with all fields."""
        result = dokploy.build_port_payload(
            "app-1",
            {
                "publishedPort": 5432,
                "targetPort": 5432,
                "protocol": "udp",
                "publishMode": "host",
            },
        )
        assert result == {
            "applicationId": "app-1",
            "publishedPort": 5432,
            "targetPort": 5432,
            "protocol": "udp",
            "publishMode": "host",
        }


class TestReconcilePorts:
    def test_reconcile_creates_new_deletes_removed_updates_existing(self):
        """reconcile_ports creates new, updates changed, deletes removed."""
        existing = [
            {
                "portId": "p1",
                "publishedPort": 8080,
                "targetPort": 80,
                "protocol": "tcp",
                "publishMode": "ingress",
            },
            {
                "portId": "p2",
                "publishedPort": 5432,
                "targetPort": 5432,
                "protocol": "tcp",
                "publishMode": "ingress",
            },
        ]
        desired = [
            {"publishedPort": 8080, "targetPort": 8000, "protocol": "tcp", "publishMode": "ingress"},
            {"publishedPort": 9090, "targetPort": 90, "protocol": "udp", "publishMode": "host"},
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "port.create": {"portId": "p3"},
        }.get(endpoint, {})

        result = dokploy.reconcile_ports(client, "app-1", existing, desired)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "port.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["portId"] == "p1"
        assert update_calls[0][0][1]["targetPort"] == 8000

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "port.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["portId"] == "p2"

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "port.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["publishedPort"] == 9090

        assert 8080 in result
        assert result[8080]["portId"] == "p1"
        assert 9090 in result
        assert result[9090]["portId"] == "p3"
        assert 5432 not in result

    def test_reconcile_no_changes(self):
        """reconcile_ports does nothing when desired matches existing."""
        existing = [
            {
                "portId": "p1",
                "publishedPort": 8080,
                "targetPort": 80,
                "protocol": "tcp",
                "publishMode": "ingress",
            },
        ]
        desired = [
            {"publishedPort": 8080, "targetPort": 80, "protocol": "tcp", "publishMode": "ingress"},
        ]
        client = MagicMock()
        dokploy.reconcile_ports(client, "app-1", existing, desired)

        assert len(client.post.call_args_list) == 0

    def test_reconcile_all_removed(self):
        """reconcile_ports deletes all when desired is empty."""
        existing = [
            {
                "portId": "p1",
                "publishedPort": 8080,
                "targetPort": 80,
                "protocol": "tcp",
                "publishMode": "ingress",
            },
        ]
        client = MagicMock()
        result = dokploy.reconcile_ports(client, "app-1", existing, [])

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "port.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["portId"] == "p1"
        assert result == {}


class TestReconcileAppPorts:
    def test_fetches_ports_from_application_one(self, tmp_path):
        """reconcile_app_ports reads ports from application.one."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {"applicationId": "app-1"},
            },
        }
        import json

        state_file.write_text(json.dumps(state))

        cfg = {
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "ports": [
                        {"publishedPort": 8080, "targetPort": 80},
                    ],
                },
            ],
        }
        client = MagicMock()
        client.get.return_value = {"ports": []}
        client.post.side_effect = lambda endpoint, payload: {"portId": "p1"}

        dokploy.reconcile_app_ports(client, cfg, state, state_file)

        client.get.assert_called_once_with("application.one", {"applicationId": "app-1"})

    def test_skips_compose_apps(self, tmp_path):
        """reconcile_app_ports skips compose apps."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {
                "stack": {"composeId": "c-1", "source": "compose"},
            },
        }
        import json

        state_file.write_text(json.dumps(state))

        cfg = {
            "apps": [
                {
                    "name": "stack",
                    "source": "compose",
                    "composePath": "./docker-compose.yml",
                },
            ],
        }
        client = MagicMock()
        dokploy.reconcile_app_ports(client, cfg, state, state_file)

        client.get.assert_not_called()


class TestPlanPortChanges:
    def test_plan_redeploy_shows_port_changes(self, tmp_path):
        """_plan_redeploy detects port additions, removals, and updates."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": {"type": "docker", "image": "nginx"},
                    "ports": [
                        {"publishedPort": 8080, "targetPort": 8000},
                        {"publishedPort": 9090, "targetPort": 90, "protocol": "udp", "publishMode": "host"},
                    ],
                },
            ],
        }
        remote_ports = [
            {
                "portId": "p1",
                "publishedPort": 8080,
                "targetPort": 80,
                "protocol": "tcp",
                "publishMode": "ingress",
            },
            {
                "portId": "p2",
                "publishedPort": 5432,
                "targetPort": 5432,
                "protocol": "tcp",
                "publishMode": "ingress",
            },
        ]

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {"env": "", "ports": remote_ports}
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        port_changes = [c for c in changes if c["resource_type"] == "port"]
        actions = {c["action"] for c in port_changes}
        assert "create" in actions
        assert "destroy" in actions
        assert "update" in actions

        created = [c for c in port_changes if c["action"] == "create"]
        assert len(created) == 1
        assert created[0]["attrs"]["publishedPort"] == 9090

        destroyed = [c for c in port_changes if c["action"] == "destroy"]
        assert len(destroyed) == 1
        assert destroyed[0]["attrs"]["publishedPort"] == 5432

        updated = [c for c in port_changes if c["action"] == "update"]
        assert len(updated) == 1
        assert "targetPort" in updated[0]["attrs"]

    def test_plan_initial_setup_shows_ports(self, tmp_path):
        """_plan_initial_setup includes port creation."""
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "ports": [
                        {"publishedPort": 8080, "targetPort": 80},
                    ],
                },
            ],
        }
        changes = []
        dokploy._plan_initial_setup(cfg, tmp_path, changes)

        port_changes = [c for c in changes if c["resource_type"] == "port"]
        assert len(port_changes) == 1
        assert port_changes[0]["action"] == "create"
        assert port_changes[0]["attrs"]["publishedPort"] == 8080
        assert port_changes[0]["attrs"]["targetPort"] == 80


class TestPlanAppConfigChanges:
    def test_plan_redeploy_shows_command_change(self, tmp_path):
        """_plan_redeploy detects command drift between config and server."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "command": "python manage.py runserver",
                },
            ],
        }

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {
                    "env": "",
                    "command": "python manage.py migrate",
                    "mounts": [],
                    "ports": [],
                }
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        settings_changes = [c for c in changes if c["resource_type"] == "settings"]
        assert len(settings_changes) == 1
        assert settings_changes[0]["action"] == "update"
        assert "command" in settings_changes[0]["attrs"]
        assert settings_changes[0]["attrs"]["command"] == (
            "python manage.py migrate",
            "python manage.py runserver",
        )

    def test_plan_redeploy_shows_replicas_change(self, tmp_path):
        """_plan_redeploy detects replicas drift."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "replicas": 3,
                },
            ],
        }

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {
                    "env": "",
                    "replicas": 1,
                    "mounts": [],
                    "ports": [],
                }
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        settings_changes = [c for c in changes if c["resource_type"] == "settings"]
        assert len(settings_changes) == 1
        assert settings_changes[0]["attrs"]["replicas"] == (1, 3)

    def test_plan_redeploy_shows_auto_deploy_change(self, tmp_path):
        """_plan_redeploy detects autoDeploy drift."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "autoDeploy": True,
                },
            ],
        }

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {
                    "env": "",
                    "autoDeploy": False,
                    "mounts": [],
                    "ports": [],
                }
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        settings_changes = [c for c in changes if c["resource_type"] == "settings"]
        assert len(settings_changes) == 1
        assert settings_changes[0]["attrs"]["autoDeploy"] == (False, True)

    def test_plan_redeploy_no_settings_change_when_matching(self, tmp_path):
        """_plan_redeploy produces no settings change when remote matches config."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "command": "serve",
                    "replicas": 2,
                },
            ],
        }

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {
                    "env": "",
                    "command": "serve",
                    "replicas": 2,
                    "mounts": [],
                    "ports": [],
                }
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        settings_changes = [c for c in changes if c["resource_type"] == "settings"]
        assert len(settings_changes) == 0

    def test_plan_redeploy_skips_compose_app_config(self, tmp_path):
        """_plan_redeploy does not check app config for compose apps."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "composeId": "comp-1",
                    "source": "compose",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "compose",
                    "composeFile": "docker-compose.yml",
                },
            ],
        }

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "compose.one":
                return {"env": ""}
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        settings_changes = [c for c in changes if c["resource_type"] == "settings"]
        assert len(settings_changes) == 0

    def test_plan_redeploy_multiple_config_diffs(self, tmp_path):
        """_plan_redeploy shows multiple setting fields in one change."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                },
            },
        }
        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "command": "new-cmd",
                    "replicas": 5,
                    "autoDeploy": True,
                },
            ],
        }

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {
                    "env": "",
                    "command": "old-cmd",
                    "replicas": 1,
                    "autoDeploy": False,
                    "mounts": [],
                    "ports": [],
                }
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, cfg, state, tmp_path, changes)

        settings_changes = [c for c in changes if c["resource_type"] == "settings"]
        assert len(settings_changes) == 1
        assert len(settings_changes[0]["attrs"]) == 3


class TestCmdApplyReconcileAppSettings:
    def test_redeploy_calls_reconcile_app_settings(self, tmp_path, monkeypatch):
        """cmd_apply calls reconcile_app_settings on redeploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        import json

        state_file.write_text(json.dumps({"projectId": "proj-1", "apps": {}}))

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda state, cfg: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_domains", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_schedules", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_mounts", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_ports", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_settings",
            lambda client, cfg, state: calls.append("reconcile_app_settings"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_settings" in calls


class TestReconcileAppSettings:
    def test_updates_command_when_different(self):
        """reconcile_app_settings updates command when it differs from remote."""
        client = MagicMock()
        client.get.return_value = {
            "command": "old-cmd",
            "replicas": 1,
            "autoDeploy": False,
        }

        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "command": "new-cmd",
                },
            ],
        }
        state = {
            "projectId": "proj-1",
            "apps": {"web": {"applicationId": "app-1"}},
        }

        dokploy.reconcile_app_settings(client, cfg, state)

        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == "application.update"
        assert call_args[0][1]["command"] == "new-cmd"

    def test_updates_replicas_and_autodeploy(self):
        """reconcile_app_settings updates replicas and autoDeploy."""
        client = MagicMock()
        client.get.return_value = {
            "replicas": 1,
            "autoDeploy": False,
        }

        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "replicas": 3,
                    "autoDeploy": True,
                },
            ],
        }
        state = {
            "projectId": "proj-1",
            "apps": {"web": {"applicationId": "app-1"}},
        }

        dokploy.reconcile_app_settings(client, cfg, state)

        call_args = client.post.call_args
        assert call_args[0][1]["replicas"] == 3
        assert call_args[0][1]["autoDeploy"] is True

    def test_no_update_when_matching(self):
        """reconcile_app_settings does not call update when remote matches."""
        client = MagicMock()
        client.get.return_value = {
            "command": "serve",
            "replicas": 2,
            "autoDeploy": True,
        }

        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                    "command": "serve",
                    "replicas": 2,
                    "autoDeploy": True,
                },
            ],
        }
        state = {
            "projectId": "proj-1",
            "apps": {"web": {"applicationId": "app-1"}},
        }

        dokploy.reconcile_app_settings(client, cfg, state)

        client.post.assert_not_called()

    def test_skips_compose_apps(self):
        """reconcile_app_settings skips compose apps."""
        client = MagicMock()

        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "compose",
                    "composeFile": "docker-compose.yml",
                },
            ],
        }
        state = {
            "projectId": "proj-1",
            "apps": {"web": {"composeId": "comp-1", "source": "compose"}},
        }

        dokploy.reconcile_app_settings(client, cfg, state)

        client.get.assert_not_called()
        client.post.assert_not_called()

    def test_skips_when_no_settings_in_config(self):
        """reconcile_app_settings skips apps with no command/replicas/autoDeploy."""
        client = MagicMock()

        cfg = {
            "project": {"name": "test", "description": "test"},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx",
                },
            ],
        }
        state = {
            "projectId": "proj-1",
            "apps": {"web": {"applicationId": "app-1"}},
        }

        dokploy.reconcile_app_settings(client, cfg, state)

        client.get.assert_not_called()
        client.post.assert_not_called()


class TestCmdApplyReconcilePorts:
    def test_redeploy_calls_reconcile_app_ports(self, tmp_path, monkeypatch):
        """cmd_apply calls reconcile_app_ports on redeploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        import json

        state_file.write_text(json.dumps({"projectId": "proj-1", "apps": {}}))

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda state, cfg: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_domains", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_schedules", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_mounts", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_ports",
            lambda client, cfg, state, sf: calls.append("reconcile_app_ports"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_ports" in calls

    def test_fresh_deploy_does_not_call_reconcile_app_ports(self, tmp_path, monkeypatch):
        """cmd_apply does NOT call reconcile_app_ports on fresh deploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_ports",
            lambda client, cfg, state, sf: calls.append("reconcile_app_ports"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_ports" not in calls


class TestDatabaseDefaults:
    def test_default_images_for_all_types(self):
        """Each supported database type has a default docker image."""
        for db_type in ("postgres", "mysql", "mariadb", "mongo", "redis"):
            assert db_type in dokploy.DATABASE_DEFAULTS

    def test_supported_types(self):
        """DATABASE_TYPES contains exactly the five supported types."""
        assert {"postgres", "mysql", "mariadb", "mongo", "redis"} == dokploy.DATABASE_TYPES


class TestBuildDatabaseCreatePayload:
    def test_postgres_payload(self):
        db_def = {
            "name": "mydb",
            "type": "postgres",
            "databaseName": "myapp",
            "databaseUser": "myuser",
            "databasePassword": "secret123",
        }
        payload = dokploy.build_database_create_payload("mydb", db_def, "env-123")
        assert payload["name"] == "mydb"
        assert payload["environmentId"] == "env-123"
        assert payload["databaseName"] == "myapp"
        assert payload["databaseUser"] == "myuser"
        assert payload["databasePassword"] == "secret123"
        assert "dockerImage" in payload

    def test_postgres_default_image(self):
        db_def = {"name": "mydb", "type": "postgres", "databaseName": "x", "databaseUser": "u", "databasePassword": "p"}
        payload = dokploy.build_database_create_payload("mydb", db_def, "env-1")
        assert payload["dockerImage"] == dokploy.DATABASE_DEFAULTS["postgres"]

    def test_custom_image(self):
        db_def = {
            "name": "mydb",
            "type": "postgres",
            "databaseName": "x",
            "databaseUser": "u",
            "databasePassword": "p",
            "dockerImage": "postgres:15",
        }
        payload = dokploy.build_database_create_payload("mydb", db_def, "env-1")
        assert payload["dockerImage"] == "postgres:15"

    def test_mysql_includes_root_password(self):
        db_def = {
            "name": "db",
            "type": "mysql",
            "databaseName": "app",
            "databaseUser": "u",
            "databasePassword": "p",
            "databaseRootPassword": "rootpw",
        }
        payload = dokploy.build_database_create_payload("db", db_def, "env-1")
        assert payload["databaseRootPassword"] == "rootpw"

    def test_mariadb_includes_root_password(self):
        db_def = {
            "name": "db",
            "type": "mariadb",
            "databaseName": "app",
            "databaseUser": "u",
            "databasePassword": "p",
            "databaseRootPassword": "rootpw",
        }
        payload = dokploy.build_database_create_payload("db", db_def, "env-1")
        assert payload["databaseRootPassword"] == "rootpw"

    def test_redis_minimal_fields(self):
        db_def = {"name": "cache", "type": "redis", "databasePassword": "p"}
        payload = dokploy.build_database_create_payload("cache", db_def, "env-1")
        assert payload["name"] == "cache"
        assert payload["databasePassword"] == "p"
        assert "databaseName" not in payload
        assert "databaseUser" not in payload

    def test_mongo_no_database_name(self):
        db_def = {"name": "mdb", "type": "mongo", "databaseUser": "u", "databasePassword": "p"}
        payload = dokploy.build_database_create_payload("mdb", db_def, "env-1")
        assert "databaseName" not in payload
        assert payload["databaseUser"] == "u"

    def test_description_included(self):
        db_def = {
            "name": "mydb",
            "type": "postgres",
            "databaseName": "x",
            "databaseUser": "u",
            "databasePassword": "p",
            "description": "Main database",
        }
        payload = dokploy.build_database_create_payload("mydb", db_def, "env-1")
        assert payload["description"] == "Main database"


class TestDatabaseApiEndpoint:
    def test_endpoint_for_each_type(self):
        """database_endpoint returns the correct API path for each type."""
        assert dokploy.database_endpoint("postgres", "create") == "postgres.create"
        assert dokploy.database_endpoint("mysql", "deploy") == "mysql.deploy"
        assert dokploy.database_endpoint("redis", "one") == "redis.one"
        assert dokploy.database_endpoint("mongo", "remove") == "mongo.remove"
        assert dokploy.database_endpoint("mariadb", "deploy") == "mariadb.deploy"

    def test_database_id_key(self):
        """database_id_key returns the correct ID field name for each type."""
        assert dokploy.database_id_key("postgres") == "postgresId"
        assert dokploy.database_id_key("mysql") == "mysqlId"
        assert dokploy.database_id_key("mariadb") == "mariadbId"
        assert dokploy.database_id_key("mongo") == "mongoId"
        assert dokploy.database_id_key("redis") == "redisId"


class TestCmdSetupDatabases:
    def _make_mock_client(self, db_responses=None):
        client = MagicMock()
        call_count = {"n": 0}
        db_responses = db_responses or {}

        def fake_post(endpoint, payload=None):
            if endpoint == "project.create":
                return {
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                }
            if endpoint == "application.create":
                name = (payload or {}).get("name", "app")
                return {"applicationId": f"app-{name}-id", "appName": f"app-{name}"}
            # Database creates
            for db_type in dokploy.DATABASE_TYPES:
                if endpoint == f"{db_type}.create":
                    db_name = (payload or {}).get("name", "db")
                    id_key = dokploy.database_id_key(db_type)
                    resp = db_responses.get(db_name, {id_key: f"{db_type}-{db_name}-id", "appName": f"{db_name}-app"})
                    return resp
                if endpoint == f"{db_type}.deploy":
                    return {}
            return {}

        client.post = MagicMock(side_effect=fake_post)
        client.get = MagicMock(return_value=[])
        return client

    def test_databases_created_during_setup(self, tmp_path, database_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, database_config, state_file)

        create_calls = [call for call in client.post.call_args_list if call[0][0] == "postgres.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["name"] == "mydb"

        redis_calls = [call for call in client.post.call_args_list if call[0][0] == "redis.create"]
        assert len(redis_calls) == 1

    def test_databases_deployed_after_creation(self, tmp_path, database_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, database_config, state_file)

        deploy_calls = [call for call in client.post.call_args_list if call[0][0] in ("postgres.deploy", "redis.deploy")]
        assert len(deploy_calls) == 2

    def test_database_state_saved(self, tmp_path, database_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, database_config, state_file)

        state = json.loads(state_file.read_text())
        assert "database" in state
        assert "mydb" in state["database"]
        assert "postgresId" in state["database"]["mydb"]
        assert "cache" in state["database"]
        assert "redisId" in state["database"]["cache"]

    def test_database_state_includes_type(self, tmp_path, database_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, database_config, state_file)

        state = json.loads(state_file.read_text())
        assert state["database"]["mydb"]["type"] == "postgres"
        assert state["database"]["cache"]["type"] == "redis"

    def test_no_databases_section_skips(self, tmp_path, minimal_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, minimal_config, state_file)

        state = json.loads(state_file.read_text())
        assert "database" not in state


class TestCmdStatusDatabases:
    def test_shows_database_status(self, tmp_path, monkeypatch, capsys):
        state = {
            "projectId": "proj-1",
            "apps": {},
            "database": {
                "mydb": {"postgresId": "pg-1", "appName": "mydb-app", "type": "postgres"},
            },
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

        client = MagicMock()
        client.get = MagicMock(return_value={"applicationStatus": "running"})

        dokploy.cmd_status(client, state_file)

        output = capsys.readouterr().out
        assert "mydb" in output
        assert "postgres" in output


class TestComputePlanDatabases:
    def _mock_client(self):
        client = MagicMock()
        client.get = MagicMock(return_value=[])
        return client

    def test_plan_initial_shows_database_creates(self, tmp_path, database_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, database_config, state_file, tmp_path)

        db_creates = [c for c in changes if c["resource_type"] == "database"]
        assert len(db_creates) == 2
        names = {c["name"] for c in db_creates}
        assert names == {"mydb", "cache"}

    def test_plan_database_attrs(self, tmp_path, database_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, database_config, state_file, tmp_path)

        pg_create = [c for c in changes if c["name"] == "mydb"][0]
        assert pg_create["attrs"]["type"] == "postgres"
        assert pg_create["action"] == "create"

    def test_plan_no_databases_no_db_changes(self, tmp_path, minimal_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, minimal_config, state_file, tmp_path)

        db_creates = [c for c in changes if c["resource_type"] == "database"]
        assert len(db_creates) == 0


class TestBuildRegistryPayloads:
    def test_build_registry_create_payload(self):
        reg_def = {
            "name": "ghcr",
            "registryUrl": "https://ghcr.io",
            "username": "myuser",
            "password": "mytoken",
        }
        payload = dokploy.build_registry_create_payload(reg_def)
        assert payload == {
            "registryName": "ghcr",
            "username": "myuser",
            "password": "mytoken",
            "registryUrl": "https://ghcr.io",
            "registryType": "cloud",
            "imagePrefix": None,
        }

    def test_build_registry_create_payload_with_image_prefix(self):
        reg_def = {
            "name": "ghcr",
            "registryUrl": "https://ghcr.io",
            "username": "myuser",
            "password": "mytoken",
            "imagePrefix": "myorg",
        }
        payload = dokploy.build_registry_create_payload(reg_def)
        assert payload["imagePrefix"] == "myorg"

    def test_build_registry_update_payload(self):
        reg_def = {
            "name": "ghcr",
            "registryUrl": "https://ghcr.io",
            "username": "myuser",
            "password": "newtoken",
        }
        payload = dokploy.build_registry_update_payload("reg-123", reg_def)
        assert payload["registryId"] == "reg-123"
        assert payload["registryName"] == "ghcr"
        assert payload["password"] == "newtoken"
        assert payload["registryType"] == "cloud"

    def test_resolve_registry_id_found(self):
        state = {"registries": {"ghcr": {"registryId": "reg-abc"}}}
        assert dokploy.resolve_registry_id(state, "ghcr") == "reg-abc"

    def test_resolve_registry_id_missing(self):
        state = {"registries": {"ghcr": {"registryId": "reg-abc"}}}
        assert dokploy.resolve_registry_id(state, "dockerhub") is None

    def test_resolve_registry_id_no_registries_section(self):
        state = {"apps": {}}
        assert dokploy.resolve_registry_id(state, "ghcr") is None


class TestValidateConfigRegistry:
    def test_app_references_unknown_registry_exits(self, minimal_config):
        minimal_config["apps"][0]["registry"] = "nonexistent"
        with pytest.raises(SystemExit):
            dokploy.validate_config(minimal_config)

    def test_no_registries_section_passes(self, minimal_config):
        dokploy.validate_config(minimal_config)

    def test_valid_registry_reference_passes(self, registry_config):
        dokploy.validate_config(registry_config)


class TestCmdSetupRegistries:
    def _make_mock_client(self, existing_registries=None):
        client = MagicMock()

        def fake_post(endpoint, payload=None):
            if endpoint == "project.create":
                return {
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                }
            if endpoint == "application.create":
                name = (payload or {}).get("name", "app")
                return {"applicationId": f"app-{name}-id", "appName": f"app-{name}"}
            if endpoint == "registry.create":
                return {"registryId": "reg-new-id"}
            if endpoint == "registry.update":
                return {}
            if endpoint == "application.update":
                return {}
            if endpoint == "application.saveDockerProvider":
                return {}
            return {}

        client.post = MagicMock(side_effect=fake_post)
        client.get = MagicMock(return_value=existing_registries or [])
        return client

    def test_registries_created_during_setup(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client(existing_registries=[])

        dokploy.cmd_setup(client, registry_config, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "registry.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["registryName"] == "ghcr"

    def test_existing_registry_reused_and_updated(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        existing = [{"registryName": "ghcr", "registryId": "reg-existing"}]
        client = self._make_mock_client(existing_registries=existing)

        dokploy.cmd_setup(client, registry_config, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "registry.create"]
        assert len(create_calls) == 0
        update_calls = [c for c in client.post.call_args_list if c[0][0] == "registry.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["registryId"] == "reg-existing"

    def test_registry_state_saved(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client(existing_registries=[])

        dokploy.cmd_setup(client, registry_config, state_file)

        state = json.loads(state_file.read_text())
        assert "registries" in state
        assert "ghcr" in state["registries"]
        assert state["registries"]["ghcr"]["registryId"] == "reg-new-id"

    def test_app_registry_association(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client(existing_registries=[])

        dokploy.cmd_setup(client, registry_config, state_file)

        update_calls = [
            c for c in client.post.call_args_list if c[0][0] == "application.update" and "registryId" in (c[0][1] or {})
        ]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["registryId"] == "reg-new-id"

    def test_no_registries_section_backward_compat(self, tmp_path, minimal_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, minimal_config, state_file)

        state = json.loads(state_file.read_text())
        assert "registries" not in state


class TestReconcileRegistries:
    def test_reconcile_creates_missing_registry(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True)
        state = {"projectId": "proj-1", "environmentId": "env-1", "apps": {}}
        state_file.write_text(json.dumps(state))

        client = MagicMock()
        client.get = MagicMock(return_value=[])
        client.post = MagicMock(return_value={"registryId": "reg-new"})

        from icarus.reconcile import reconcile_registries

        reconcile_registries(client, registry_config, state, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "registry.create"]
        assert len(create_calls) == 1
        assert state["registries"]["ghcr"]["registryId"] == "reg-new"

    def test_reconcile_updates_existing_registry(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True)
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {},
            "registries": {"ghcr": {"registryId": "reg-old"}},
        }
        state_file.write_text(json.dumps(state))

        client = MagicMock()
        client.get = MagicMock(return_value=[{"registryName": "ghcr", "registryId": "reg-old"}])
        client.post = MagicMock(return_value={})

        from icarus.reconcile import reconcile_registries

        reconcile_registries(client, registry_config, state, state_file)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "registry.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["registryId"] == "reg-old"


class TestReconcileRegistriesConnectionTest:
    def test_tests_connection_after_create(self, registry_config):
        state = {"projectId": "proj-1", "environmentId": "env-1", "apps": {}}
        state_file = MagicMock()

        client = MagicMock()
        client.get = MagicMock(return_value=[])
        client.post = MagicMock(return_value={"registryId": "reg-new"})

        from icarus.reconcile import reconcile_registries

        reconcile_registries(client, registry_config, state, state_file)

        test_calls = [c for c in client.post.call_args_list if c[0][0] == "registry.testRegistry"]
        assert len(test_calls) == 1

    def test_failed_connection_test_exits(self, registry_config):
        state = {"projectId": "proj-1", "environmentId": "env-1", "apps": {}}
        state_file = MagicMock()

        client = MagicMock()
        client.get = MagicMock(return_value=[])

        def fake_post(endpoint, payload=None):
            if endpoint == "registry.testRegistry":
                raise httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=MagicMock())
            return {"registryId": "reg-new"}

        client.post = MagicMock(side_effect=fake_post)

        from icarus.reconcile import reconcile_registries

        with pytest.raises(SystemExit):
            reconcile_registries(client, registry_config, state, state_file)


class TestReconcileDestinationsConnectionTest:
    def test_tests_connection_after_create(self):
        client = MagicMock()
        client.get.return_value = []
        client.post.return_value = {"destinationId": "dest-new"}
        cfg = {
            "destinations": [
                {
                    "name": "s3",
                    "accessKey": "k",
                    "secretAccessKey": "s",
                    "bucket": "b",
                    "region": "r",
                    "endpoint": "e",
                }
            ],
        }
        state = {"destinations": {}}
        state_file = MagicMock()

        dokploy.reconcile_destinations(client, cfg, state, state_file)

        test_calls = [c for c in client.post.call_args_list if c[0][0] == "destination.testConnection"]
        assert len(test_calls) == 1

    def test_failed_connection_test_exits(self):
        client = MagicMock()
        client.get.return_value = []
        cfg = {
            "destinations": [
                {
                    "name": "s3",
                    "accessKey": "k",
                    "secretAccessKey": "s",
                    "bucket": "b",
                    "region": "r",
                    "endpoint": "e",
                }
            ],
        }
        state = {"destinations": {}}
        state_file = MagicMock()

        def fake_post(endpoint, payload=None):
            if endpoint == "destination.testConnection":
                raise httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=MagicMock())
            return {"destinationId": "dest-new"}

        client.post = MagicMock(side_effect=fake_post)

        with pytest.raises(SystemExit):
            dokploy.reconcile_destinations(client, cfg, state, state_file)


class TestReconcileAppRegistry:
    def test_reconcile_app_registry_changed(self):
        client = MagicMock()
        client.get = MagicMock(return_value={"registryId": "reg-old"})
        client.post = MagicMock(return_value={})

        state = {
            "apps": {"app": {"applicationId": "app-1"}},
            "registries": {"ghcr": {"registryId": "reg-new"}},
        }
        cfg = {
            "apps": [{"name": "app", "source": "docker", "dockerImage": "img", "registry": "ghcr"}],
        }

        from icarus.reconcile import reconcile_app_registry

        reconcile_app_registry(client, cfg, state)

        update_calls = [
            c for c in client.post.call_args_list if c[0][0] == "application.update" and "registryId" in (c[0][1] or {})
        ]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["registryId"] == "reg-new"

    def test_reconcile_app_registry_removed(self):
        client = MagicMock()
        client.get = MagicMock(return_value={"registryId": "reg-old"})
        client.post = MagicMock(return_value={})

        state = {
            "apps": {"app": {"applicationId": "app-1"}},
        }
        cfg = {
            "apps": [{"name": "app", "source": "docker", "dockerImage": "img"}],
        }

        from icarus.reconcile import reconcile_app_registry

        reconcile_app_registry(client, cfg, state)

        update_calls = [
            c for c in client.post.call_args_list if c[0][0] == "application.update" and c[0][1].get("registryId") is None
        ]
        assert len(update_calls) == 1

    def test_reconcile_app_registry_unchanged(self):
        client = MagicMock()
        client.get = MagicMock(return_value={"registryId": "reg-1"})
        client.post = MagicMock(return_value={})

        state = {
            "apps": {"app": {"applicationId": "app-1"}},
            "registries": {"ghcr": {"registryId": "reg-1"}},
        }
        cfg = {
            "apps": [{"name": "app", "source": "docker", "dockerImage": "img", "registry": "ghcr"}],
        }

        from icarus.reconcile import reconcile_app_registry

        reconcile_app_registry(client, cfg, state)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "application.update"]
        assert len(update_calls) == 0


class TestComputePlanRegistries:
    def _mock_client(self):
        client = MagicMock()
        client.get = MagicMock(return_value=[])
        return client

    def test_plan_initial_shows_registry_creates(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, registry_config, state_file, tmp_path)

        reg_creates = [c for c in changes if c["resource_type"] == "registry"]
        assert len(reg_creates) == 1
        assert reg_creates[0]["name"] == "ghcr"
        assert reg_creates[0]["action"] == "create"
        assert reg_creates[0]["attrs"]["registryUrl"] == "https://ghcr.io"

    def test_plan_initial_app_shows_registry_attr(self, tmp_path, registry_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, registry_config, state_file, tmp_path)

        app_creates = [c for c in changes if c["resource_type"] == "application"]
        assert len(app_creates) == 1
        assert app_creates[0]["attrs"].get("registry") == "ghcr"

    def test_plan_no_registries_no_changes(self, tmp_path, minimal_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, minimal_config, state_file, tmp_path)

        reg_creates = [c for c in changes if c["resource_type"] == "registry"]
        assert len(reg_creates) == 0


class TestBuildSecurityPayload:
    def test_payload_structure(self):
        result = dokploy.build_security_payload("app-1", {"username": "admin", "password": "s3cret"})
        assert result == {
            "applicationId": "app-1",
            "username": "admin",
            "password": "s3cret",
        }


class TestSecurityReconciliation:
    def test_reconcile_creates_new_deletes_removed_updates_existing(self):
        """reconcile_security creates new, updates changed, deletes removed."""
        existing = [
            {"securityId": "s1", "username": "admin", "password": "old-pass"},
            {"securityId": "s2", "username": "removed-user", "password": "gone"},
        ]
        desired = [
            {"username": "admin", "password": "new-pass"},
            {"username": "deploy", "password": "d3pl0y"},
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "security.create": {"securityId": "s3"},
        }.get(endpoint, {})

        result = dokploy.reconcile_security(client, "app-1", existing, desired)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "security.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["securityId"] == "s1"
        assert update_calls[0][0][1]["username"] == "admin"
        assert update_calls[0][0][1]["password"] == "new-pass"

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "security.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["securityId"] == "s2"

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "security.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["username"] == "deploy"

        assert "admin" in result
        assert result["admin"]["securityId"] == "s1"
        assert "deploy" in result
        assert result["deploy"]["securityId"] == "s3"
        assert "removed-user" not in result

    def test_reconcile_no_changes(self):
        """reconcile_security does nothing when desired matches existing."""
        existing = [
            {"securityId": "s1", "username": "admin", "password": "s3cret"},
        ]
        desired = [
            {"username": "admin", "password": "s3cret"},
        ]
        client = MagicMock()
        dokploy.reconcile_security(client, "app-1", existing, desired)

        assert len(client.post.call_args_list) == 0

    def test_reconcile_all_removed(self):
        """reconcile_security deletes all when desired is empty."""
        existing = [
            {"securityId": "s1", "username": "admin", "password": "s3cret"},
            {"securityId": "s2", "username": "deploy", "password": "d3pl0y"},
        ]
        desired = []
        client = MagicMock()

        result = dokploy.reconcile_security(client, "app-1", existing, desired)

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "security.delete"]
        assert len(delete_calls) == 2
        assert result == {}


class TestReconcileAppSecurity:
    def test_fetches_security_from_application_one(self, tmp_path):
        """reconcile_app_security fetches remote security and reconciles."""
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"web": {"applicationId": "app-1", "appName": "web"}},
        }
        state_file.write_text(json.dumps(state))

        cfg = {
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "security": [{"username": "admin", "password": "s3cret"}],
                }
            ]
        }

        client = MagicMock()
        client.get.return_value = {"security": []}
        client.post.return_value = {"securityId": "s1"}

        dokploy.reconcile_app_security(client, cfg, state, state_file)

        client.get.assert_called_once_with("application.one", {"applicationId": "app-1"})
        create_calls = [c for c in client.post.call_args_list if c[0][0] == "security.create"]
        assert len(create_calls) == 1

        saved = json.loads(state_file.read_text())
        assert "security" in saved["apps"]["web"]
        assert "admin" in saved["apps"]["web"]["security"]

    def test_skips_compose_apps(self, tmp_path):
        """reconcile_app_security skips compose apps."""
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"web": {"composeId": "comp-1", "appName": "web", "source": "compose"}},
        }
        state_file.write_text(json.dumps(state))

        cfg = {"apps": [{"name": "web", "source": "compose", "composeFile": "docker-compose.yml"}]}

        client = MagicMock()
        dokploy.reconcile_app_security(client, cfg, state, state_file)

        client.get.assert_not_called()

    def test_no_security_skips(self, tmp_path):
        """reconcile_app_security skips apps without security config or state."""
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"web": {"applicationId": "app-1", "appName": "web"}},
        }
        state_file.write_text(json.dumps(state))

        cfg = {"apps": [{"name": "web", "source": "docker", "dockerImage": "nginx:alpine"}]}

        client = MagicMock()
        dokploy.reconcile_app_security(client, cfg, state, state_file)

        client.get.assert_not_called()


class TestCmdSetupSecurity:
    def test_security_create_calls(self, tmp_path):
        """cmd_setup creates security entries and stores securityId in state."""
        cfg = {
            "project": {"name": "test", "description": "test", "deploy_order": [["web"]]},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:alpine",
                    "security": [
                        {"username": "admin", "password": "s3cret"},
                        {"username": "deploy", "password": "d3pl0y"},
                    ],
                }
            ],
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)

        call_count = {"n": 0}

        def mock_post(endpoint, payload):
            if endpoint == "project.create":
                return {
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                }
            if endpoint == "application.create":
                return {"applicationId": "app-1", "appName": "web"}
            if endpoint == "security.create":
                call_count["n"] += 1
                return {"securityId": f"sec-{call_count['n']}"}
            return {}

        client = MagicMock()
        client.post.side_effect = mock_post

        dokploy.cmd_setup(client, cfg, state_file)

        security_calls = [c for c in client.post.call_args_list if c[0][0] == "security.create"]
        assert len(security_calls) == 2
        assert security_calls[0][0][1]["username"] == "admin"
        assert security_calls[1][0][1]["username"] == "deploy"

        saved = json.loads(state_file.read_text())
        assert "security" in saved["apps"]["web"]
        assert saved["apps"]["web"]["security"]["admin"]["securityId"] == "sec-1"
        assert saved["apps"]["web"]["security"]["deploy"]["securityId"] == "sec-2"

    def test_no_security_skips(self, tmp_path):
        """cmd_setup skips security when not configured."""
        cfg = {
            "project": {"name": "test", "description": "test", "deploy_order": [["web"]]},
            "apps": [{"name": "web", "source": "docker", "dockerImage": "nginx:alpine"}],
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)

        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "project.create": {
                "project": {"projectId": "proj-1"},
                "environment": {"environmentId": "env-1"},
            },
            "application.create": {"applicationId": "app-1", "appName": "web"},
        }.get(endpoint, {})

        dokploy.cmd_setup(client, cfg, state_file)

        security_calls = [c for c in client.post.call_args_list if c[0][0] == "security.create"]
        assert len(security_calls) == 0


class TestCmdApplyReconcileSecurity:
    def test_redeploy_calls_reconcile_app_security(self, tmp_path, monkeypatch):
        """cmd_apply calls reconcile_app_security on redeploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{}")

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)
        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda state, cfg: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_domains", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_schedules", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_mounts", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_ports", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_settings", lambda client, cfg, state: None)
        monkeypatch.setattr(icarus_commands, "reconcile_registries", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_registry", lambda client, cfg, state: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_redirects", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_security",
            lambda client, cfg, state, sf: calls.append("reconcile_app_security"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_security" in calls

    def test_fresh_deploy_does_not_call_reconcile_app_security(self, tmp_path, monkeypatch):
        """cmd_apply does NOT call reconcile_app_security on fresh deploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "test.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_security",
            lambda client, cfg, state, sf: calls.append("reconcile_app_security"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_security" not in calls


class TestComputePlanSecurity:
    def _mock_client(self):
        client = MagicMock()
        client.get = MagicMock(return_value=[])
        return client

    def test_plan_initial_shows_security_creates(self, tmp_path, security_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, security_config, state_file, tmp_path)

        sec_creates = [c for c in changes if c["resource_type"] == "security"]
        assert len(sec_creates) == 2
        usernames = {c["name"] for c in sec_creates}
        assert usernames == {"admin", "deploy"}
        assert all(c["action"] == "create" for c in sec_creates)
        assert all(c["parent"] == "web" for c in sec_creates)

    def test_plan_no_security_no_changes(self, tmp_path, minimal_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, minimal_config, state_file, tmp_path)

        sec_creates = [c for c in changes if c["resource_type"] == "security"]
        assert len(sec_creates) == 0

    def test_plan_redeploy_shows_security_changes(self, tmp_path, security_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "projectId": "proj-1",
            "environmentId": "env-1",
            "apps": {"web": {"applicationId": "app-1", "appName": "web"}},
        }
        state_file.write_text(json.dumps(state))

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "project.all":
                return [{"projectId": "proj-1"}]
            if endpoint == "application.one":
                return {
                    "applicationId": "app-1",
                    "security": [
                        {"securityId": "s1", "username": "admin", "password": "old-pass"},
                        {"securityId": "s2", "username": "removed-user", "password": "gone"},
                    ],
                }
            return []

        client.get.side_effect = mock_get

        changes = dokploy.compute_plan(client, security_config, state_file, tmp_path)

        sec_changes = [c for c in changes if c["resource_type"] == "security"]
        actions = {c["action"] for c in sec_changes}
        assert "create" in actions
        assert "update" in actions
        assert "destroy" in actions

        created = [c for c in sec_changes if c["action"] == "create"]
        assert len(created) == 1
        assert created[0]["name"] == "deploy"

        updated = [c for c in sec_changes if c["action"] == "update"]
        assert len(updated) == 1
        assert updated[0]["name"] == "admin"
        assert "password" in updated[0]["attrs"]

        destroyed = [c for c in sec_changes if c["action"] == "destroy"]
        assert len(destroyed) == 1
        assert destroyed[0]["name"] == "removed-user"


class TestSecuritySchemaValidation:
    def test_security_config_valid(self, security_config):
        """Security config fixture loads and validates."""
        assert "apps" in security_config
        assert len(security_config["apps"]) == 1
        app = security_config["apps"][0]
        assert "security" in app
        assert len(app["security"]) == 2

    def test_security_parsed_correctly(self, security_config):
        app = security_config["apps"][0]
        usernames = {s["username"] for s in app["security"]}
        assert usernames == {"admin", "deploy"}


# ── Redirects ──────────────────────────────────────────────────────


class TestBuildRedirectPayload:
    def test_basic_redirect(self):
        """build_redirect_payload returns correct structure."""
        result = dokploy.build_redirect_payload(
            "app-1",
            {
                "regex": "^/old/(.*)",
                "replacement": "/new/$1",
                "permanent": True,
            },
        )
        assert result == {
            "applicationId": "app-1",
            "regex": "^/old/(.*)",
            "replacement": "/new/$1",
            "permanent": True,
        }

    def test_temporary_redirect(self):
        """build_redirect_payload handles permanent=False."""
        result = dokploy.build_redirect_payload(
            "app-2",
            {
                "regex": "^/blog$",
                "replacement": "https://blog.example.com",
                "permanent": False,
            },
        )
        assert result["permanent"] is False
        assert result["applicationId"] == "app-2"


class TestReconcileRedirects:
    def test_reconcile_creates_new_deletes_removed_updates_existing(self):
        """reconcile_redirects creates new, updates changed, deletes removed."""
        existing = [
            {
                "redirectId": "r1",
                "regex": "^/keep/(.*)",
                "replacement": "/old/$1",
                "permanent": True,
            },
            {
                "redirectId": "r2",
                "regex": "^/remove$",
                "replacement": "/gone",
                "permanent": False,
            },
        ]
        desired = [
            {
                "regex": "^/keep/(.*)",
                "replacement": "/new/$1",
                "permanent": True,
            },
            {
                "regex": "^/add$",
                "replacement": "/added",
                "permanent": False,
            },
        ]
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "redirects.create": {"redirectId": "r3"},
        }.get(endpoint, {})

        result = dokploy.reconcile_redirects(client, "app-1", existing, desired)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "redirects.update"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1]["redirectId"] == "r1"
        assert update_calls[0][0][1]["replacement"] == "/new/$1"

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "redirects.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["redirectId"] == "r2"

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "redirects.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["regex"] == "^/add$"

        assert "^/keep/(.*)" in result
        assert result["^/keep/(.*)"]["redirectId"] == "r1"
        assert "^/add$" in result
        assert result["^/add$"]["redirectId"] == "r3"
        assert "^/remove$" not in result

    def test_reconcile_no_changes(self):
        """reconcile_redirects does nothing when desired matches existing."""
        existing = [
            {
                "redirectId": "r1",
                "regex": "^/path$",
                "replacement": "/dest",
                "permanent": True,
            },
        ]
        desired = [
            {
                "regex": "^/path$",
                "replacement": "/dest",
                "permanent": True,
            },
        ]
        client = MagicMock()
        dokploy.reconcile_redirects(client, "app-1", existing, desired)

        assert len(client.post.call_args_list) == 0

    def test_reconcile_all_removed(self):
        """reconcile_redirects deletes all when desired is empty."""
        existing = [
            {
                "redirectId": "r1",
                "regex": "^/path$",
                "replacement": "/dest",
                "permanent": True,
            },
        ]
        client = MagicMock()
        result = dokploy.reconcile_redirects(client, "app-1", existing, [])

        delete_calls = [c for c in client.post.call_args_list if c[0][0] == "redirects.delete"]
        assert len(delete_calls) == 1
        assert delete_calls[0][0][1]["redirectId"] == "r1"
        assert result == {}


class TestReconcileAppRedirects:
    def test_fetches_redirects_from_application_one(self, tmp_path):
        """reconcile_app_redirects fetches existing from application.one."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {"web": {"applicationId": "app-1"}},
        }
        state_file.write_text(json.dumps(state))
        cfg = {
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "redirects": [
                        {
                            "regex": "^/new$",
                            "replacement": "/dest",
                            "permanent": True,
                        }
                    ],
                }
            ]
        }
        client = MagicMock()
        client.get.return_value = {"redirects": []}
        client.post.return_value = {"redirectId": "r1"}

        dokploy.reconcile_app_redirects(client, cfg, state, state_file)

        client.get.assert_called_once_with("application.one", {"applicationId": "app-1"})
        create_calls = [c for c in client.post.call_args_list if c[0][0] == "redirects.create"]
        assert len(create_calls) == 1

    def test_skips_compose_apps(self, tmp_path):
        """reconcile_app_redirects skips compose apps."""
        state_file = tmp_path / "state.json"
        state = {
            "projectId": "proj-1",
            "apps": {"web": {"composeId": "comp-1", "source": "compose"}},
        }
        state_file.write_text(json.dumps(state))
        cfg = {
            "apps": [
                {
                    "name": "web",
                    "source": "compose",
                    "redirects": [
                        {
                            "regex": "^/test$",
                            "replacement": "/dest",
                            "permanent": True,
                        }
                    ],
                }
            ]
        }
        client = MagicMock()

        dokploy.reconcile_app_redirects(client, cfg, state, state_file)

        client.get.assert_not_called()


class TestSetupRedirects:
    def test_redirects_create_calls(self, tmp_path):
        """cmd_setup creates redirects via API."""
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        cfg = {
            "project": {"name": "test", "description": "test", "deploy_order": [["web"]]},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:latest",
                    "redirects": [
                        {
                            "regex": "^/old$",
                            "replacement": "/new",
                            "permanent": True,
                        }
                    ],
                }
            ],
        }
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "project.create": {
                "project": {"projectId": "proj-1"},
                "environment": {"environmentId": "env-1"},
            },
            "application.create": {"applicationId": "app-1", "appName": "web-abc"},
            "redirects.create": {"redirectId": "r1"},
        }.get(endpoint, {})
        client.get.return_value = []

        dokploy.cmd_setup(client, cfg, state_file, tmp_path)

        redirect_calls = [c for c in client.post.call_args_list if c[0][0] == "redirects.create"]
        assert len(redirect_calls) == 1
        assert redirect_calls[0][0][1]["regex"] == "^/old$"

    def test_redirects_stored_in_state(self, tmp_path):
        """cmd_setup stores redirect IDs in state."""
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        cfg = {
            "project": {"name": "test", "description": "test", "deploy_order": [["web"]]},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:latest",
                    "redirects": [
                        {
                            "regex": "^/old$",
                            "replacement": "/new",
                            "permanent": True,
                        }
                    ],
                }
            ],
        }
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "project.create": {
                "project": {"projectId": "proj-1"},
                "environment": {"environmentId": "env-1"},
            },
            "application.create": {"applicationId": "app-1", "appName": "web-abc"},
            "redirects.create": {"redirectId": "r1"},
        }.get(endpoint, {})
        client.get.return_value = []

        dokploy.cmd_setup(client, cfg, state_file, tmp_path)

        import json

        saved_state = json.loads(state_file.read_text())
        assert saved_state["apps"]["web"]["redirects"]["^/old$"]["redirectId"] == "r1"

    def test_no_redirects_skips(self, tmp_path):
        """cmd_setup skips redirect creation when none defined."""
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)
        cfg = {
            "project": {"name": "test", "description": "test", "deploy_order": [["web"]]},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:latest",
                }
            ],
        }
        client = MagicMock()
        client.post.side_effect = lambda endpoint, payload: {
            "project.create": {
                "project": {"projectId": "proj-1"},
                "environment": {"environmentId": "env-1"},
            },
            "application.create": {"applicationId": "app-1", "appName": "web-abc"},
        }.get(endpoint, {})
        client.get.return_value = []

        dokploy.cmd_setup(client, cfg, state_file, tmp_path)

        redirect_calls = [c for c in client.post.call_args_list if c[0][0] == "redirects.create"]
        assert len(redirect_calls) == 0


class TestCmdApplyReconcileRedirects:
    def test_redeploy_calls_reconcile_app_redirects(self, tmp_path, monkeypatch):
        """cmd_apply calls reconcile_app_redirects on redeploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"
        state_file.parent.mkdir(parents=True)

        state_file.write_text(json.dumps({"projectId": "proj-1", "apps": {}}))

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "validate_state", lambda client, state: True)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(icarus_commands, "cleanup_stale_routes", lambda state, cfg: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_domains", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_schedules", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_mounts", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_ports", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_security", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_settings", lambda client, cfg, state: None)
        monkeypatch.setattr(icarus_commands, "reconcile_registries", lambda client, cfg, state, sf: None)
        monkeypatch.setattr(icarus_commands, "reconcile_app_registry", lambda client, cfg, state: None)
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_redirects",
            lambda client, cfg, state, sf: calls.append("reconcile_app_redirects"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_redirects" in calls

    def test_fresh_deploy_does_not_call_reconcile_app_redirects(self, tmp_path, monkeypatch):
        """cmd_apply does NOT call reconcile_app_redirects on fresh deploy."""
        calls = []
        state_file = tmp_path / ".dokploy-state" / "prod.json"

        monkeypatch.setattr(icarus_commands, "cmd_check", lambda repo_root: None)
        monkeypatch.setattr(icarus_commands, "cmd_setup", lambda client, cfg, sf, repo_root=None: None)
        monkeypatch.setattr(icarus_commands, "cmd_env", lambda client, cfg, sf, repo_root, env_file_override=None: None)
        monkeypatch.setattr(
            icarus_commands, "cmd_trigger", lambda client, cfg, sf, repo_root=None, env_file_override=None, redeploy=False: None
        )
        monkeypatch.setattr(
            icarus_commands,
            "reconcile_app_redirects",
            lambda client, cfg, state, sf: calls.append("reconcile_app_redirects"),
        )

        dokploy.cmd_apply(
            repo_root=tmp_path,
            client="fake-client",
            cfg={"project": {}},
            state_file=state_file,
        )

        assert "reconcile_app_redirects" not in calls


class TestPlanRedirects:
    @pytest.fixture
    def redirects_config(self):
        return {
            "project": {"name": "test-proj", "deploy_order": [["web"]]},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:latest",
                    "redirects": [
                        {
                            "regex": "^/old$",
                            "replacement": "/new",
                            "permanent": True,
                        }
                    ],
                }
            ],
        }

    def _mock_client(self):
        client = MagicMock()
        client.get.return_value = {}
        return client

    def test_initial_setup_shows_redirect_creates(self, tmp_path, redirects_config):
        """compute_plan shows redirect creates on fresh setup."""
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, redirects_config, state_file, tmp_path)

        redirect_creates = [c for c in changes if c["resource_type"] == "redirect"]
        assert len(redirect_creates) == 1
        assert redirect_creates[0]["action"] == "create"
        assert redirect_creates[0]["name"] == "^/old$"
        assert redirect_creates[0]["parent"] == "web"
        assert redirect_creates[0]["attrs"]["permanent"] is True

    def test_redeploy_shows_redirect_changes(self, tmp_path, redirects_config):
        """_plan_redeploy detects redirect additions, removals, and updates."""
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                    "redirects": {"^/old$": {"redirectId": "r1"}},
                },
            },
        }

        client = MagicMock()

        def mock_get(endpoint, params=None):
            if endpoint == "application.one":
                return {
                    "env": "",
                    "redirects": [
                        {
                            "redirectId": "r1",
                            "regex": "^/old$",
                            "replacement": "/old-dest",
                            "permanent": False,
                        }
                    ],
                }
            return {}

        client.get.side_effect = mock_get

        changes = []
        dokploy._plan_redeploy(client, redirects_config, state, tmp_path, changes)

        redirect_changes = [c for c in changes if c["resource_type"] == "redirect"]
        assert len(redirect_changes) == 1
        assert redirect_changes[0]["action"] == "update"
        assert "replacement" in redirect_changes[0]["attrs"]

    def test_no_redirects_no_changes(self, tmp_path):
        """compute_plan with no redirects produces no redirect changes."""
        cfg = {
            "project": {"name": "test-proj", "deploy_order": [["web"]]},
            "apps": [
                {
                    "name": "web",
                    "source": "docker",
                    "dockerImage": "nginx:latest",
                }
            ],
        }
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._mock_client()

        changes = dokploy.compute_plan(client, cfg, state_file, tmp_path)

        redirect_changes = [c for c in changes if c["resource_type"] == "redirect"]
        assert len(redirect_changes) == 0


# ---------------------------------------------------------------------------
# DokployClient retry with exponential backoff
# ---------------------------------------------------------------------------

BASE_URL = "https://dokploy.test"
API_KEY = "test-key"


def _make_retry_client(router: respx.Router, max_retries: int = 3) -> dokploy.DokployClient:
    """Create a DokployClient with mocked transport for retry tests."""
    client = dokploy.DokployClient(BASE_URL, API_KEY, max_retries=max_retries)
    mock_transport = httpx.MockTransport(router.handler)
    client.client = httpx.Client(
        transport=mock_transport,
        base_url=BASE_URL,
        headers={"x-api-key": API_KEY},
        timeout=60.0,
    )
    return client


class TestRetryOnTransientErrors:
    def test_retry_on_429(self):
        """429 responses are retried up to max_retries times."""
        router = respx.Router()
        route = router.get("/api/project.all").mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(429),
                httpx.Response(200, json=[{"id": 1}]),
            ]
        )
        client = _make_retry_client(router)
        with patch("icarus.client.time.sleep"):
            result = client.get("project.all")
        assert result == [{"id": 1}]
        assert route.call_count == 3

    def test_retry_on_500(self):
        """5xx responses are retried."""
        router = respx.Router()
        route = router.get("/api/project.all").mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(502),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = _make_retry_client(router)
        with patch("icarus.client.time.sleep"):
            result = client.get("project.all")
        assert result == {"ok": True}
        assert route.call_count == 3

    def test_no_retry_on_400(self):
        """400 Bad Request fails immediately without retry."""
        router = respx.Router()
        route = router.post("/api/application.create").mock(return_value=httpx.Response(400, json={"error": "bad request"}))
        client = _make_retry_client(router)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.post("application.create", {"name": "test"})
        assert exc_info.value.response.status_code == 400
        assert route.call_count == 1

    def test_no_retry_on_404(self):
        """404 Not Found fails immediately without retry."""
        router = respx.Router()
        route = router.get("/api/application.one").mock(return_value=httpx.Response(404))
        client = _make_retry_client(router)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get("application.one")
        assert exc_info.value.response.status_code == 404
        assert route.call_count == 1

    def test_exponential_backoff_timing(self):
        """Sleep durations follow exponential backoff: 1s, 2s, 4s."""
        router = respx.Router()
        router.get("/api/project.all").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(503),
                httpx.Response(200, json=[]),
            ]
        )
        client = _make_retry_client(router)
        with patch("icarus.client.time.sleep") as mock_sleep:
            client.get("project.all")
        assert mock_sleep.call_args_list == [call(1), call(2)]

    def test_max_retries_exhausted(self):
        """Raises after max_retries+1 total attempts."""
        router = respx.Router()
        route = router.get("/api/project.all").mock(return_value=httpx.Response(500))
        client = _make_retry_client(router, max_retries=3)
        with patch("icarus.client.time.sleep"), pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get("project.all")
        assert exc_info.value.response.status_code == 500
        assert route.call_count == 4  # 1 initial + 3 retries

    def test_configurable_max_retries(self):
        """max_retries=1 allows only 1 retry (2 total attempts)."""
        router = respx.Router()
        route = router.get("/api/project.all").mock(return_value=httpx.Response(500))
        client = _make_retry_client(router, max_retries=1)
        with patch("icarus.client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            client.get("project.all")
        assert route.call_count == 2

    def test_success_no_retry(self):
        """Successful responses return immediately without sleeping."""
        router = respx.Router()
        route = router.get("/api/project.all").mock(return_value=httpx.Response(200, json=[]))
        client = _make_retry_client(router)
        with patch("icarus.client.time.sleep") as mock_sleep:
            result = client.get("project.all")
        assert result == []
        assert route.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Backup destination payload tests
# ---------------------------------------------------------------------------


class TestBuildDestinationCreatePayload:
    def test_required_fields(self):
        dest_def = {
            "name": "s3-backups",
            "accessKey": "AKIAIOSFODNN7EXAMPLE",
            "secretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "bucket": "my-backups",
            "region": "us-east-1",
            "endpoint": "https://s3.amazonaws.com",
        }
        payload = dokploy.build_destination_create_payload(dest_def)
        assert payload["name"] == "s3-backups"
        assert payload["accessKey"] == "AKIAIOSFODNN7EXAMPLE"
        assert payload["secretAccessKey"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert payload["bucket"] == "my-backups"
        assert payload["region"] == "us-east-1"
        assert payload["endpoint"] == "https://s3.amazonaws.com"

    def test_no_extra_fields(self):
        dest_def = {
            "name": "s3",
            "accessKey": "k",
            "secretAccessKey": "s",
            "bucket": "b",
            "region": "r",
            "endpoint": "e",
        }
        payload = dokploy.build_destination_create_payload(dest_def)
        assert set(payload.keys()) == {"name", "accessKey", "secretAccessKey", "bucket", "region", "endpoint"}


class TestBuildDestinationUpdatePayload:
    def test_includes_destination_id(self):
        dest_def = {
            "name": "s3-backups",
            "accessKey": "k",
            "secretAccessKey": "s",
            "bucket": "b",
            "region": "r",
            "endpoint": "e",
        }
        payload = dokploy.build_destination_update_payload("dest-123", dest_def)
        assert payload["destinationId"] == "dest-123"
        assert payload["name"] == "s3-backups"


# ---------------------------------------------------------------------------
# Backup schedule payload tests
# ---------------------------------------------------------------------------


class TestBuildBackupCreatePayload:
    def test_postgres_backup(self):
        backup_def = {
            "schedule": "0 0 * * *",
            "prefix": "daily",
            "destination": "s3-backups",
            "enabled": True,
            "keepLatestCount": 7,
        }
        payload = dokploy.build_backup_create_payload(
            backup_def,
            db_id="pg-123",
            db_type="postgres",
            db_name="mydb",
            destination_id="dest-456",
        )
        assert payload["schedule"] == "0 0 * * *"
        assert payload["prefix"] == "daily"
        assert payload["enabled"] is True
        assert payload["keepLatestCount"] == 7
        assert payload["destinationId"] == "dest-456"
        assert payload["databaseType"] == "postgres"
        assert payload["postgresId"] == "pg-123"
        assert payload["database"] == "mydb"
        assert payload["backupType"] == "database"

    def test_mysql_backup(self):
        backup_def = {
            "schedule": "0 0 * * 0",
            "prefix": "weekly",
            "destination": "s3",
            "keepLatestCount": 4,
        }
        payload = dokploy.build_backup_create_payload(
            backup_def,
            db_id="mysql-1",
            db_type="mysql",
            db_name="prod",
            destination_id="dest-1",
        )
        assert payload["mysqlId"] == "mysql-1"
        assert payload["databaseType"] == "mysql"
        assert payload["enabled"] is True  # default

    def test_defaults(self):
        backup_def = {
            "schedule": "0 0 * * *",
            "prefix": "bk",
            "destination": "s3",
        }
        payload = dokploy.build_backup_create_payload(
            backup_def,
            db_id="pg-1",
            db_type="postgres",
            db_name="db",
            destination_id="dest-1",
        )
        assert payload["enabled"] is True
        assert "keepLatestCount" not in payload


# ---------------------------------------------------------------------------
# Backup setup integration tests
# ---------------------------------------------------------------------------


class TestCmdSetupDestinations:
    def _make_mock_client(self):
        client = MagicMock()

        def fake_post(endpoint, payload=None):
            if endpoint == "project.create":
                return {
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                }
            if endpoint == "application.create":
                name = (payload or {}).get("name", "app")
                return {"applicationId": f"app-{name}-id", "appName": f"app-{name}"}
            if endpoint == "destination.create":
                return {"destinationId": "dest-new-id"}
            for db_type in dokploy.DATABASE_TYPES:
                if endpoint == f"{db_type}.create":
                    db_name = (payload or {}).get("name", "db")
                    id_key = dokploy.database_id_key(db_type)
                    return {id_key: f"{db_type}-{db_name}-id", "appName": f"{db_name}-app"}
                if endpoint == f"{db_type}.deploy":
                    return {}
            if endpoint == "backup.create":
                return {"backupId": "backup-new-id"}
            return {}

        client.post = MagicMock(side_effect=fake_post)
        client.get = MagicMock(return_value=[])
        return client

    def test_destinations_created_during_setup(self, tmp_path, backup_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, backup_config, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "destination.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["name"] == "s3-backups"

    def test_destination_state_saved(self, tmp_path, backup_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, backup_config, state_file)

        state = json.loads(state_file.read_text())
        assert "destinations" in state
        assert "s3-backups" in state["destinations"]
        assert state["destinations"]["s3-backups"]["destinationId"] == "dest-new-id"

    def test_existing_destination_reused(self, tmp_path, backup_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()
        client.get = MagicMock(
            side_effect=lambda endpoint, params=None: (
                [{"destinationId": "dest-existing", "name": "s3-backups"}] if endpoint == "destination.all" else []
            )
        )

        dokploy.cmd_setup(client, backup_config, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "destination.create"]
        assert len(create_calls) == 0
        update_calls = [c for c in client.post.call_args_list if c[0][0] == "destination.update"]
        assert len(update_calls) == 1

    def test_backups_created_for_databases(self, tmp_path, backup_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, backup_config, state_file)

        backup_calls = [c for c in client.post.call_args_list if c[0][0] == "backup.create"]
        assert len(backup_calls) == 1
        payload = backup_calls[0][0][1]
        assert payload["schedule"] == "0 0 * * *"
        assert payload["prefix"] == "daily"
        assert payload["databaseType"] == "postgres"

    def test_backup_state_saved(self, tmp_path, backup_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, backup_config, state_file)

        state = json.loads(state_file.read_text())
        assert "backups" in state["database"]["mydb"]
        assert "daily" in state["database"]["mydb"]["backups"]
        assert state["database"]["mydb"]["backups"]["daily"]["backupId"] == "backup-new-id"

    def test_no_backups_for_db_without_config(self, tmp_path, backup_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, backup_config, state_file)

        state = json.loads(state_file.read_text())
        assert "backups" not in state["database"]["cache"]


# ---------------------------------------------------------------------------
# Backup plan tests
# ---------------------------------------------------------------------------


class TestPlanBackups:
    def test_initial_setup_includes_destinations(self, backup_config, tmp_path):
        changes = []
        dokploy._plan_initial_setup(backup_config, tmp_path, changes)

        dest_changes = [c for c in changes if c["resource_type"] == "destination"]
        assert len(dest_changes) == 1
        assert dest_changes[0]["action"] == "create"
        assert dest_changes[0]["name"] == "s3-backups"

    def test_initial_setup_includes_backups(self, backup_config, tmp_path):
        changes = []
        dokploy._plan_initial_setup(backup_config, tmp_path, changes)

        backup_changes = [c for c in changes if c["resource_type"] == "backup"]
        assert len(backup_changes) == 1
        assert backup_changes[0]["action"] == "create"
        assert backup_changes[0]["name"] == "daily"
        assert backup_changes[0]["parent"] == "mydb"
        assert backup_changes[0]["attrs"]["schedule"] == "0 0 * * *"

    def test_no_backups_for_db_without_config(self, backup_config, tmp_path):
        changes = []
        dokploy._plan_initial_setup(backup_config, tmp_path, changes)

        backup_changes = [c for c in changes if c["resource_type"] == "backup"]
        parents = [c["parent"] for c in backup_changes]
        assert "cache" not in parents


# ---------------------------------------------------------------------------
# Backup reconciliation tests
# ---------------------------------------------------------------------------


class TestReconcileDestinations:
    def test_creates_new_destination(self):
        client = MagicMock()
        client.get.return_value = []
        client.post.return_value = {"destinationId": "dest-new"}
        cfg = {
            "destinations": [
                {
                    "name": "s3",
                    "accessKey": "k",
                    "secretAccessKey": "s",
                    "bucket": "b",
                    "region": "r",
                    "endpoint": "e",
                }
            ],
        }
        state = {"destinations": {}}
        state_file = MagicMock()

        dokploy.reconcile_destinations(client, cfg, state, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "destination.create"]
        assert len(create_calls) == 1

    def test_updates_existing_destination(self):
        client = MagicMock()
        client.get.return_value = [{"destinationId": "dest-1", "name": "s3"}]
        client.post.return_value = {}
        cfg = {
            "destinations": [
                {
                    "name": "s3",
                    "accessKey": "k",
                    "secretAccessKey": "s",
                    "bucket": "b",
                    "region": "r",
                    "endpoint": "e",
                }
            ],
        }
        state = {"destinations": {"s3": {"destinationId": "dest-1"}}}
        state_file = MagicMock()

        dokploy.reconcile_destinations(client, cfg, state, state_file)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "destination.update"]
        assert len(update_calls) == 1

    def test_skips_when_no_destinations(self):
        client = MagicMock()
        cfg = {}
        state = {}
        state_file = MagicMock()

        dokploy.reconcile_destinations(client, cfg, state, state_file)

        client.post.assert_not_called()


class TestReconcileDatabaseBackups:
    def test_creates_new_backup(self):
        client = MagicMock()
        client.get.return_value = []
        client.post.return_value = {"backupId": "bk-1"}
        cfg = {
            "database": [
                {
                    "name": "mydb",
                    "type": "postgres",
                    "databaseName": "myapp",
                    "databaseUser": "u",
                    "databasePassword": "p",
                    "backups": [
                        {
                            "schedule": "0 0 * * *",
                            "prefix": "daily",
                            "destination": "s3",
                            "keepLatestCount": 7,
                        }
                    ],
                }
            ],
        }
        state = {
            "database": {
                "mydb": {"postgresId": "pg-1", "appName": "mydb-app", "type": "postgres"},
            },
            "destinations": {"s3": {"destinationId": "dest-1"}},
        }
        state_file = MagicMock()

        dokploy.reconcile_database_backups(client, cfg, state, state_file)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "backup.create"]
        assert len(create_calls) == 1

    def test_skips_db_without_backups(self):
        client = MagicMock()
        cfg = {
            "database": [
                {
                    "name": "cache",
                    "type": "redis",
                    "databasePassword": "p",
                }
            ],
        }
        state = {
            "database": {"cache": {"redisId": "r-1", "appName": "cache-app", "type": "redis"}},
            "destinations": {},
        }
        state_file = MagicMock()

        dokploy.reconcile_database_backups(client, cfg, state, state_file)

        client.post.assert_not_called()


class TestBuildDatabaseUpdatePayload:
    def test_no_change_returns_none(self):
        remote = {"dockerImage": "postgres:16"}
        db_def = {"name": "mydb", "type": "postgres", "databasePassword": "p"}
        payload = dokploy.build_database_update_payload("pg-1", "postgres", db_def, remote)
        assert payload is None

    def test_image_change_detected(self):
        remote = {"dockerImage": "postgres:15"}
        db_def = {"name": "mydb", "type": "postgres", "databasePassword": "p", "dockerImage": "postgres:16"}
        payload = dokploy.build_database_update_payload("pg-1", "postgres", db_def, remote)
        assert payload == {"postgresId": "pg-1", "dockerImage": "postgres:16"}

    def test_description_change_detected(self):
        remote = {"dockerImage": "postgres:16", "description": "old"}
        db_def = {
            "name": "mydb",
            "type": "postgres",
            "databasePassword": "p",
            "description": "new",
        }
        payload = dokploy.build_database_update_payload("pg-1", "postgres", db_def, remote)
        assert payload == {"postgresId": "pg-1", "description": "new"}

    def test_default_image_used_when_unset(self):
        remote = {"dockerImage": "redis:6"}
        db_def = {"name": "cache", "type": "redis", "databasePassword": "p"}
        payload = dokploy.build_database_update_payload("r-1", "redis", db_def, remote)
        assert payload == {"redisId": "r-1", "dockerImage": "redis:7"}


class TestReconcileDatabases:
    def test_updates_image_and_rebuilds(self):
        client = MagicMock()
        client.get.return_value = {"dockerImage": "postgres:15"}
        cfg = {
            "database": [
                {
                    "name": "mydb",
                    "type": "postgres",
                    "databaseName": "myapp",
                    "databaseUser": "u",
                    "databasePassword": "p",
                    "dockerImage": "postgres:16",
                }
            ],
        }
        state = {"database": {"mydb": {"postgresId": "pg-1", "appName": "mydb-app", "type": "postgres"}}}
        state_file = MagicMock()

        dokploy.reconcile_databases(client, cfg, state, state_file)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "postgres.update"]
        rebuild_calls = [c for c in client.post.call_args_list if c[0][0] == "postgres.rebuild"]
        assert len(update_calls) == 1
        assert update_calls[0][0][1] == {"postgresId": "pg-1", "dockerImage": "postgres:16"}
        assert len(rebuild_calls) == 1
        assert rebuild_calls[0][0][1] == {"postgresId": "pg-1"}

    def test_no_drift_no_calls(self):
        client = MagicMock()
        client.get.return_value = {"dockerImage": "postgres:16"}
        cfg = {
            "database": [
                {
                    "name": "mydb",
                    "type": "postgres",
                    "databaseName": "myapp",
                    "databaseUser": "u",
                    "databasePassword": "p",
                }
            ],
        }
        state = {"database": {"mydb": {"postgresId": "pg-1", "appName": "mydb-app", "type": "postgres"}}}
        state_file = MagicMock()

        dokploy.reconcile_databases(client, cfg, state, state_file)

        client.post.assert_not_called()

    def test_description_only_change_no_rebuild(self):
        client = MagicMock()
        client.get.return_value = {"dockerImage": "redis:7", "description": "old"}
        cfg = {
            "database": [
                {
                    "name": "cache",
                    "type": "redis",
                    "databasePassword": "p",
                    "description": "new",
                }
            ],
        }
        state = {"database": {"cache": {"redisId": "r-1", "appName": "cache-app", "type": "redis"}}}
        state_file = MagicMock()

        dokploy.reconcile_databases(client, cfg, state, state_file)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "redis.update"]
        rebuild_calls = [c for c in client.post.call_args_list if c[0][0] == "redis.rebuild"]
        assert len(update_calls) == 1
        assert len(rebuild_calls) == 0

    def test_skips_when_no_databases(self):
        client = MagicMock()
        cfg = {}
        state = {}
        state_file = MagicMock()

        dokploy.reconcile_databases(client, cfg, state, state_file)

        client.get.assert_not_called()
        client.post.assert_not_called()

    def test_skips_db_not_yet_in_state(self):
        client = MagicMock()
        cfg = {
            "database": [
                {
                    "name": "newdb",
                    "type": "postgres",
                    "databaseName": "myapp",
                    "databaseUser": "u",
                    "databasePassword": "p",
                }
            ],
        }
        state = {"database": {}}
        state_file = MagicMock()

        dokploy.reconcile_databases(client, cfg, state, state_file)

        client.get.assert_not_called()
        client.post.assert_not_called()


class TestBuildCertificateCreatePayload:
    def test_reads_cert_and_key_files(self, tmp_path):
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\nDATA\n-----END CERTIFICATE-----\n")
        key_file.write_text("-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----\n")

        cert_def = {
            "name": "wildcard",
            "certFile": str(cert_file),
            "keyFile": str(key_file),
            "autoRenew": False,
        }
        payload = dokploy.build_certificate_create_payload(cert_def, "org-123", tmp_path)

        assert payload["name"] == "wildcard"
        assert payload["certificateData"] == cert_file.read_text()
        assert payload["privateKey"] == key_file.read_text()
        assert payload["organizationId"] == "org-123"
        assert payload["autoRenew"] is False

    def test_relative_paths_resolved_from_repo_root(self, tmp_path):
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        (certs_dir / "cert.pem").write_text("CERT")
        (certs_dir / "key.pem").write_text("KEY")

        cert_def = {
            "name": "test",
            "certFile": "certs/cert.pem",
            "keyFile": "certs/key.pem",
        }
        payload = dokploy.build_certificate_create_payload(cert_def, "org-1", tmp_path)

        assert payload["certificateData"] == "CERT"
        assert payload["privateKey"] == "KEY"

    def test_auto_renew_defaults_to_none(self, tmp_path):
        (tmp_path / "c.pem").write_text("C")
        (tmp_path / "k.pem").write_text("K")

        cert_def = {"name": "x", "certFile": "c.pem", "keyFile": "k.pem"}
        payload = dokploy.build_certificate_create_payload(cert_def, "org-1", tmp_path)

        assert payload.get("autoRenew") is None


class TestBuildDomainPayloadCustomCert:
    def test_custom_cert_includes_resolver(self):
        dom = {
            "host": "app.example.com",
            "port": 8000,
            "https": True,
            "certificateType": "custom",
            "certificate": "wildcard-example",
        }
        payload = dokploy.build_domain_payload("app-1", dom)

        assert payload["certificateType"] == "custom"
        assert payload["customCertResolver"] == "wildcard-example"

    def test_letsencrypt_no_resolver(self):
        dom = {
            "host": "app.example.com",
            "port": 8000,
            "https": True,
            "certificateType": "letsencrypt",
        }
        payload = dokploy.build_domain_payload("app-1", dom)

        assert payload["certificateType"] == "letsencrypt"
        assert "customCertResolver" not in payload


class TestCmdSetupCertificates:
    def _make_mock_client(self):
        client = MagicMock()

        def fake_post(endpoint, payload=None):
            if endpoint == "project.create":
                return {
                    "project": {"projectId": "proj-1", "organizationId": "org-1"},
                    "environment": {"environmentId": "env-1"},
                }
            if endpoint == "application.create":
                name = (payload or {}).get("name", "app")
                return {"applicationId": f"app-{name}-id", "appName": f"app-{name}"}
            if endpoint == "certificates.create":
                return {"certificateId": "cert-new-id"}
            return {}

        client.post = MagicMock(side_effect=fake_post)
        client.get = MagicMock(return_value=[])
        return client

    def test_certificates_created_during_setup(self, tmp_path, certificate_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        (certs_dir / "wildcard.pem").write_text("CERT_DATA")
        (certs_dir / "wildcard.key").write_text("KEY_DATA")
        client = self._make_mock_client()

        dokploy.cmd_setup(client, certificate_config, state_file, repo_root=tmp_path)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "certificates.create"]
        assert len(create_calls) == 1
        assert create_calls[0][0][1]["name"] == "wildcard-example"

    def test_certificate_state_saved(self, tmp_path, certificate_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        (certs_dir / "wildcard.pem").write_text("CERT_DATA")
        (certs_dir / "wildcard.key").write_text("KEY_DATA")
        client = self._make_mock_client()

        dokploy.cmd_setup(client, certificate_config, state_file, repo_root=tmp_path)

        state = json.loads(state_file.read_text())
        assert "certificates" in state
        assert "wildcard-example" in state["certificates"]
        assert state["certificates"]["wildcard-example"]["certificateId"] == "cert-new-id"

    def test_existing_certificate_reused(self, tmp_path, certificate_config):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        certs_dir = tmp_path / "certs"
        certs_dir.mkdir()
        (certs_dir / "wildcard.pem").write_text("CERT_DATA")
        (certs_dir / "wildcard.key").write_text("KEY_DATA")
        client = self._make_mock_client()
        client.get = MagicMock(
            side_effect=lambda endpoint, params=None: (
                [{"certificateId": "cert-existing", "name": "wildcard-example"}] if endpoint == "certificates.all" else []
            )
        )

        dokploy.cmd_setup(client, certificate_config, state_file, repo_root=tmp_path)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "certificates.create"]
        assert len(create_calls) == 0


class TestReconcileCertificates:
    def test_creates_new_certificate(self, tmp_path):
        client = MagicMock()
        client.get.return_value = []
        client.post.return_value = {"certificateId": "cert-new"}
        cfg = {
            "certificates": [
                {
                    "name": "wildcard",
                    "certFile": "cert.pem",
                    "keyFile": "key.pem",
                }
            ],
        }
        (tmp_path / "cert.pem").write_text("CERT")
        (tmp_path / "key.pem").write_text("KEY")
        state = {"certificates": {}, "organizationId": "org-1"}
        state_file = MagicMock()

        dokploy.reconcile_certificates(client, cfg, state, state_file, repo_root=tmp_path)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "certificates.create"]
        assert len(create_calls) == 1

    def test_skips_existing_certificate(self):
        client = MagicMock()
        client.get.return_value = [{"certificateId": "cert-1", "name": "wildcard"}]
        cfg = {
            "certificates": [
                {
                    "name": "wildcard",
                    "certFile": "cert.pem",
                    "keyFile": "key.pem",
                }
            ],
        }
        state = {"certificates": {"wildcard": {"certificateId": "cert-1"}}, "organizationId": "org-1"}
        state_file = MagicMock()

        dokploy.reconcile_certificates(client, cfg, state, state_file, repo_root=Path("/tmp"))

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "certificates.create"]
        assert len(create_calls) == 0

    def test_skips_when_no_certificates(self):
        client = MagicMock()
        cfg = {}
        state = {}
        state_file = MagicMock()

        dokploy.reconcile_certificates(client, cfg, state, state_file, repo_root=Path("/tmp"))

        client.post.assert_not_called()

    def test_deletes_removed_certificate(self):
        client = MagicMock()
        client.get.return_value = []
        client.post.return_value = {}
        cfg = {"certificates": []}
        state = {
            "certificates": {"stale": {"certificateId": "cert-stale"}},
            "organizationId": "org-1",
        }
        state_file = MagicMock()

        dokploy.reconcile_certificates(client, cfg, state, state_file, repo_root=Path("/tmp"))

        remove_calls = [c for c in client.post.call_args_list if c[0][0] == "certificates.remove"]
        assert len(remove_calls) == 1
        assert remove_calls[0][0][1] == {"certificateId": "cert-stale"}
        assert "stale" not in state["certificates"]

    def test_keeps_certificate_still_in_config(self):
        client = MagicMock()
        client.get.return_value = [{"certificateId": "cert-1", "name": "wildcard"}]
        cfg = {
            "certificates": [
                {
                    "name": "wildcard",
                    "certFile": "cert.pem",
                    "keyFile": "key.pem",
                }
            ],
        }
        state = {"certificates": {"wildcard": {"certificateId": "cert-1"}}, "organizationId": "org-1"}
        state_file = MagicMock()

        dokploy.reconcile_certificates(client, cfg, state, state_file, repo_root=Path("/tmp"))

        remove_calls = [c for c in client.post.call_args_list if c[0][0] == "certificates.remove"]
        assert len(remove_calls) == 0
        assert "wildcard" in state["certificates"]


class TestPlanCertificates:
    def test_initial_setup_shows_certificate_creates(self, certificate_config, tmp_path):
        changes = []
        dokploy._plan_initial_setup(certificate_config, tmp_path, changes)

        cert_changes = [c for c in changes if c["resource_type"] == "certificate"]
        assert len(cert_changes) == 1
        assert cert_changes[0]["action"] == "create"
        assert cert_changes[0]["name"] == "wildcard-example"

    def test_initial_setup_domain_shows_custom_cert(self, certificate_config, tmp_path):
        changes = []
        dokploy._plan_initial_setup(certificate_config, tmp_path, changes)

        domain_changes = [c for c in changes if c["resource_type"] == "domain"]
        assert len(domain_changes) == 1
        assert domain_changes[0]["attrs"]["certificateType"] == "custom"
        assert domain_changes[0]["attrs"]["certificate"] == "wildcard-example"


class TestDomainReconciliationCustomCert:
    def test_reconcile_domain_with_custom_cert(self):
        client = MagicMock()
        client.post.return_value = {"domainId": "dom-1"}
        desired = [
            {
                "host": "app.example.com",
                "port": 8000,
                "https": True,
                "certificateType": "custom",
                "certificate": "wildcard-example",
            }
        ]
        result = dokploy.reconcile_domains(client, "app-1", [], desired)

        create_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.create"]
        assert len(create_calls) == 1
        payload = create_calls[0][0][1]
        assert payload["certificateType"] == "custom"
        assert payload["customCertResolver"] == "wildcard-example"

    def test_reconcile_detects_cert_type_change(self):
        client = MagicMock()
        client.post.return_value = {}
        existing = [
            {
                "host": "app.example.com",
                "port": 8000,
                "https": True,
                "certificateType": "letsencrypt",
                "domainId": "dom-1",
            }
        ]
        desired = [
            {
                "host": "app.example.com",
                "port": 8000,
                "https": True,
                "certificateType": "custom",
                "certificate": "wildcard-example",
            }
        ]
        dokploy.reconcile_domains(client, "app-1", existing, desired)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "domain.update"]
        assert len(update_calls) == 1
        payload = update_calls[0][0][1]
        assert payload["certificateType"] == "custom"
        assert payload["customCertResolver"] == "wildcard-example"


class TestMergeAppEnv:
    def test_custom_keys_override_base(self):
        base = "FOO=from_base\nBAR=keep\n"
        custom = "FOO=from_custom\n"
        result = dokploy.merge_app_env(base, custom)
        assert "FOO=from_custom" in result
        assert "FOO=from_base" not in result
        assert "BAR=keep" in result

    def test_no_custom_returns_base(self):
        base = "FOO=bar\n"
        result = dokploy.merge_app_env(base, "")
        assert "FOO=bar" in result

    def test_duplicate_keys_custom_wins(self):
        base = "KEY=old\nOTHER=val\n"
        custom = "KEY=new\n"
        result = dokploy.merge_app_env(base, custom)
        lines = [ln for ln in result.splitlines() if ln.startswith("KEY=")]
        assert lines == ["KEY=new"]

    def test_new_custom_keys_are_appended(self):
        base = "FOO=bar\n"
        custom = "NEW=thing\n"
        result = dokploy.merge_app_env(base, custom)
        assert "FOO=bar" in result
        assert "NEW=thing" in result


class TestParseEnvToDict:
    def test_basic_parse(self):
        result = dokploy.parse_env_to_dict("FOO=bar\nBAZ=qux\n")
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_comments_and_blanks_skipped(self):
        result = dokploy.parse_env_to_dict("# comment\n\nFOO=bar\n")
        assert result == {"FOO": "bar"}

    def test_value_with_equals(self):
        result = dokploy.parse_env_to_dict("DB_URL=postgres://u:p@h/db?opt=1\n")
        assert result == {"DB_URL": "postgres://u:p@h/db?opt=1"}

    def test_last_wins_on_duplicates(self):
        result = dokploy.parse_env_to_dict("KEY=first\nKEY=second\n")
        assert result["KEY"] == "second"

    def test_empty_returns_empty_dict(self):
        assert dokploy.parse_env_to_dict("") == {}


class TestResolveAppEnvs:
    def test_env_target_app_resolved(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")
        cfg = {
            "project": {"env_targets": ["app"]},
            "apps": [{"name": "app"}],
        }
        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        result = dokploy.resolve_app_envs(cfg, state, env_file)
        assert "app" in result
        assert "FOO=bar" in result["app"]

    def test_per_app_env_only(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SHARED=val\n")
        cfg = {
            "project": {"env_targets": []},
            "apps": [{"name": "app", "env": "CUSTOM=thing\n"}],
        }
        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        result = dokploy.resolve_app_envs(cfg, state, env_file)
        assert "app" in result
        assert "CUSTOM=thing" in result["app"]

    def test_missing_env_file_skips_targets(self, tmp_path):
        env_file = tmp_path / "nonexistent.env"
        cfg = {
            "project": {"env_targets": ["app"]},
            "apps": [{"name": "app"}],
        }
        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        result = dokploy.resolve_app_envs(cfg, state, env_file)
        assert result == {}

    def test_per_app_env_resolved_when_file_missing(self, tmp_path):
        env_file = tmp_path / "nonexistent.env"
        cfg = {
            "project": {"env_targets": []},
            "apps": [{"name": "app", "env": "CUSTOM=thing\n"}],
        }
        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        result = dokploy.resolve_app_envs(cfg, state, env_file)
        assert "app" in result
        assert "CUSTOM=thing" in result["app"]

    def test_ref_resolution(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        cfg = {
            "project": {"env_targets": []},
            "apps": [{"name": "web", "env": "REDIS_URL=redis://{redis}:6379/0\n"}],
        }
        state = {
            "apps": {
                "web": {"applicationId": "app-web", "appName": "web-gen"},
                "redis": {"applicationId": "app-redis", "appName": "redis-abc123"},
            }
        }
        result = dokploy.resolve_app_envs(cfg, state, env_file)
        assert "web" in result
        assert "redis-abc123" in result["web"]

    def test_no_env_no_targets_returns_empty(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")
        cfg = {
            "project": {},
            "apps": [{"name": "app"}],
        }
        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        result = dokploy.resolve_app_envs(cfg, state, env_file)
        assert result == {}

    def test_per_app_merge_into_shared(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SHARED=yes\nFOO=from_shared\n")
        cfg = {
            "project": {"env_targets": ["app"]},
            "apps": [{"name": "app", "env": "FOO=from_app\n"}],
        }
        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        result = dokploy.resolve_app_envs(cfg, state, env_file)
        assert "FOO=from_app" in result["app"]
        assert "FOO=from_shared" not in result["app"]
        assert "SHARED=yes" in result["app"]


class TestSyncServiceEnvs:
    def test_skips_when_ssh_host_unset(self, monkeypatch, capsys):
        monkeypatch.setattr(icarus_ssh, "config", MagicMock(side_effect=lambda key, default="": default))
        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        dokploy.sync_service_envs(state, {"app": "FOO=bar\n"})
        output = capsys.readouterr().out
        assert "skip" in output.lower()

    def test_calls_docker_service_update(self, monkeypatch):
        def fake_config(key, default=""):
            if key == "DOKPLOY_SSH_HOST":
                return "server.example.com"
            return default

        monkeypatch.setattr(icarus_ssh, "config", MagicMock(side_effect=fake_config))

        stdout_mock = MagicMock()
        stdout_mock.read.return_value = b""
        ssh_mock = MagicMock()
        ssh_mock.exec_command.return_value = (None, stdout_mock, None)
        ssh_class = MagicMock(return_value=ssh_mock)
        monkeypatch.setattr(icarus_ssh.paramiko, "SSHClient", ssh_class)

        state = {"apps": {"app": {"applicationId": "app-1", "appName": "app-gen"}}}
        dokploy.sync_service_envs(state, {"app": "FOO=bar\n"})

        commands_run = [c[0][0] for c in ssh_mock.exec_command.call_args_list]
        assert any("docker service update" in cmd and "--env-add" in cmd and "app-gen" in cmd for cmd in commands_run)

    def test_skips_compose_apps(self, monkeypatch):
        def fake_config(key, default=""):
            if key == "DOKPLOY_SSH_HOST":
                return "server.example.com"
            return default

        monkeypatch.setattr(icarus_ssh, "config", MagicMock(side_effect=fake_config))

        stdout_mock = MagicMock()
        stdout_mock.read.return_value = b""
        ssh_mock = MagicMock()
        ssh_mock.exec_command.return_value = (None, stdout_mock, None)
        ssh_class = MagicMock(return_value=ssh_mock)
        monkeypatch.setattr(icarus_ssh.paramiko, "SSHClient", ssh_class)

        state = {"apps": {"app": {"composeId": "c-1", "appName": "app-gen", "source": "compose"}}}
        dokploy.sync_service_envs(state, {"app": "FOO=bar\n"})

        commands_run = [c[0][0] for c in ssh_mock.exec_command.call_args_list]
        assert not any("docker service update" in cmd for cmd in commands_run)

    def test_empty_app_envs_no_ssh(self, monkeypatch):
        ssh_class = MagicMock()
        monkeypatch.setattr(
            icarus_ssh,
            "config",
            MagicMock(side_effect=lambda key, default="": "host.example.com" if key == "DOKPLOY_SSH_HOST" else default),
        )
        monkeypatch.setattr(icarus_ssh.paramiko, "SSHClient", ssh_class)

        state = {"apps": {}}
        dokploy.sync_service_envs(state, {})

        ssh_class.assert_not_called()


# ---------------------------------------------------------------------------
# build_compose_update_payload
# ---------------------------------------------------------------------------


class TestBuildComposeUpdatePayload:
    def test_raw_returns_inline_content(self, tmp_path):
        """sourceType absent (default raw) reads compose file content from disk."""
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\nservices:\n  web:\n    image: nginx\n")
        app_def = {"name": "app", "source": "compose", "composeFile": "docker-compose.yml"}
        result = dokploy.build_compose_update_payload("comp-1", app_def, None, None, tmp_path)
        assert result["composeId"] == "comp-1"
        assert result["sourceType"] == "raw"
        assert "composeFile" in result
        assert "nginx" in result["composeFile"]
        assert "repository" not in result
        assert "githubId" not in result

    def test_github_returns_repo_coordinates(self):
        """sourceType: github sends repo fields and composePath, no disk read."""
        app_def = {
            "name": "app",
            "source": "compose",
            "sourceType": "github",
            "composeFile": "docker-compose.yml",
        }
        github_cfg = {"owner": "org", "repository": "my-app", "branch": "main"}
        result = dokploy.build_compose_update_payload("comp-1", app_def, github_cfg, "gh-123", None)
        assert result == {
            "composeId": "comp-1",
            "sourceType": "github",
            "repository": "my-app",
            "owner": "org",
            "branch": "main",
            "githubId": "gh-123",
            "composePath": "docker-compose.yml",
        }
        assert "composeFile" not in result

    def test_github_does_not_read_disk(self, tmp_path):
        """sourceType: github never touches the filesystem for the compose file."""
        app_def = {
            "name": "app",
            "source": "compose",
            "sourceType": "github",
            "composeFile": "nonexistent.yml",
        }
        github_cfg = {"owner": "org", "repository": "repo", "branch": "main"}
        # If resolve_compose_file were called it would sys.exit because the file is missing.
        result = dokploy.build_compose_update_payload("comp-1", app_def, github_cfg, "gh-99", tmp_path)
        assert result["sourceType"] == "github"
        assert result["composePath"] == "nonexistent.yml"


# ---------------------------------------------------------------------------
# validate_config — compose + sourceType: github
# ---------------------------------------------------------------------------


class TestValidateConfigComposeGithub:
    def test_compose_github_without_github_block_exits(self, minimal_config):
        """A compose app with sourceType: github requires a top-level github: block."""
        minimal_config["apps"].append(
            {
                "name": "svc",
                "source": "compose",
                "sourceType": "github",
                "composeFile": "docker-compose.yml",
            }
        )
        minimal_config["project"]["deploy_order"] = [["app", "svc"]]
        with pytest.raises(SystemExit):
            dokploy.validate_config(minimal_config)

    def test_compose_github_with_github_block_passes(self, minimal_config):
        """A compose app with sourceType: github passes when github: block present."""
        minimal_config["apps"].append(
            {
                "name": "svc",
                "source": "compose",
                "sourceType": "github",
                "composeFile": "docker-compose.yml",
            }
        )
        minimal_config["project"]["deploy_order"] = [["app", "svc"]]
        minimal_config["github"] = {"owner": "org", "repository": "repo", "branch": "main"}
        dokploy.validate_config(minimal_config)

    def test_compose_raw_without_github_block_passes(self, minimal_config):
        """A raw compose app does not require a github: block."""
        minimal_config["apps"].append(
            {
                "name": "svc",
                "source": "compose",
                "composeFile": "docker-compose.yml",
            }
        )
        minimal_config["project"]["deploy_order"] = [["app", "svc"]]
        dokploy.validate_config(minimal_config)


# ---------------------------------------------------------------------------
# cmd_setup — compose with sourceType: github
# ---------------------------------------------------------------------------


class TestCmdSetupComposeGithub:
    def _make_mock_client(self):
        client = MagicMock()

        def fake_post(endpoint, payload=None):
            if endpoint == "project.create":
                return {
                    "project": {"projectId": "proj-1"},
                    "environment": {"environmentId": "env-1"},
                }
            if endpoint == "compose.create":
                return {"composeId": "comp-gh-1", "appName": "app-gen"}
            if endpoint == "compose.update":
                return {}
            return {}

        def fake_get(endpoint, params=None):
            if endpoint == "github.githubProviders":
                return [{"githubId": "gh-123"}]
            if endpoint == "github.getGithubRepositories":
                return [{"owner": {"login": "pythoninthegrass"}, "name": "my-app"}]
            return []

        client.post = MagicMock(side_effect=fake_post)
        client.get = MagicMock(side_effect=fake_get)
        return client

    def test_compose_update_payload_uses_github_source(self, tmp_path, github_compose_config):
        """cmd_setup sends sourceType: github + repo coordinates for compose apps with sourceType: github."""
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, github_compose_config, state_file)

        update_calls = [c for c in client.post.call_args_list if c[0][0] == "compose.update"]
        assert len(update_calls) == 1
        payload = update_calls[0][0][1]
        assert payload["sourceType"] == "github"
        assert payload["repository"] == "my-app"
        assert payload["owner"] == "pythoninthegrass"
        assert payload["branch"] == "main"
        assert payload["githubId"] == "gh-123"
        assert payload["composePath"] == "docker-compose.yml"
        assert "composeFile" not in payload

    def test_compose_create_called_before_update(self, tmp_path, github_compose_config):
        """compose.create is called before compose.update during setup."""
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, github_compose_config, state_file)

        endpoints = [c[0][0] for c in client.post.call_args_list]
        create_idx = next(i for i, e in enumerate(endpoints) if e == "compose.create")
        update_idx = next(i for i, e in enumerate(endpoints) if e == "compose.update")
        assert create_idx < update_idx

    def test_github_provider_lookup_performed(self, tmp_path, github_compose_config):
        """cmd_setup fetches githubProviders when a github: block is present."""
        state_file = tmp_path / ".dokploy-state" / "test.json"
        client = self._make_mock_client()

        dokploy.cmd_setup(client, github_compose_config, state_file)

        get_calls = [c[0][0] for c in client.get.call_args_list]
        assert "github.githubProviders" in get_calls


class TestCmdRunSchedule:
    def _state_file_with_schedule(self, tmp_path):
        state_file = tmp_path / ".dokploy-state" / "test.json"
        state = {
            "projectId": "proj-1",
            "apps": {
                "web": {
                    "applicationId": "app-1",
                    "appName": "app-web-abc",
                    "schedules": {"nightly": {"scheduleId": "sched-1"}},
                }
            },
        }
        dokploy.save_state(state, state_file, quiet=True)
        return state_file

    def test_triggers_schedule_by_name(self, tmp_path):
        state_file = self._state_file_with_schedule(tmp_path)
        client = MagicMock()
        client.post.return_value = {}

        dokploy.cmd_run_schedule(client, state_file, "web", "nightly")

        run_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.runManually"]
        assert len(run_calls) == 1
        assert run_calls[0][0][1] == {"scheduleId": "sched-1"}

    def test_auto_selects_single_app(self, tmp_path):
        state_file = self._state_file_with_schedule(tmp_path)
        client = MagicMock()
        client.post.return_value = {}

        dokploy.cmd_run_schedule(client, state_file, None, "nightly")

        run_calls = [c for c in client.post.call_args_list if c[0][0] == "schedule.runManually"]
        assert len(run_calls) == 1

    def test_unknown_schedule_exits(self, tmp_path, capsys):
        state_file = self._state_file_with_schedule(tmp_path)
        client = MagicMock()

        with pytest.raises(SystemExit):
            dokploy.cmd_run_schedule(client, state_file, "web", "nonexistent")
        assert "Unknown schedule" in capsys.readouterr().out
        client.post.assert_not_called()

    def test_unknown_app_exits(self, tmp_path, capsys):
        state_file = self._state_file_with_schedule(tmp_path)
        client = MagicMock()

        with pytest.raises(SystemExit):
            dokploy.cmd_run_schedule(client, state_file, "nonexistent", "nightly")
        assert "Unknown app" in capsys.readouterr().out
        client.post.assert_not_called()
