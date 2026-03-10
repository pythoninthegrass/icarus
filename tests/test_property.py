"""Property-based tests for main.py pure functions using Hypothesis."""

import copy
import importlib.util
import pytest
import re
import sys
import yaml
from hypothesis import HealthCheck, assume, given, settings, strategies as st
from pathlib import Path

_STRATEGIES = Path(__file__).resolve().parent / "strategies.py"
_strat_spec = importlib.util.spec_from_file_location("strategies", _STRATEGIES)
strategies = importlib.util.module_from_spec(_strat_spec)
_strat_spec.loader.exec_module(strategies)

app_name = strategies.app_name
ref_compatible_name = strategies.ref_compatible_name
dokploy_config = strategies.dokploy_config
env_content = strategies.env_content
exclude_prefixes = strategies.exclude_prefixes
state_dict = strategies.state_dict

# Import main.py as a module despite it being a PEP 723 script.
_SCRIPT = Path(__file__).resolve().parent.parent / "main.py"
_spec = importlib.util.spec_from_file_location("dokploy", _SCRIPT)
dokploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dokploy)

pytestmark = pytest.mark.property


class TestFilterEnvProperties:
    @given(content=env_content(), prefixes=exclude_prefixes())
    def test_idempotent(self, content, prefixes):
        """filter_env(filter_env(x, p), p) == filter_env(x, p)."""
        once = dokploy.filter_env(content, prefixes)
        twice = dokploy.filter_env(once, prefixes)
        assert once == twice

    @given(content=env_content(), prefixes=exclude_prefixes())
    def test_no_excluded_keys_survive(self, content, prefixes):
        """Output never contains a line starting with any excluded prefix."""
        result = dokploy.filter_env(content, prefixes)
        for line in result.splitlines():
            key = line.split("=", 1)[0].strip()
            for prefix in prefixes:
                assert not key.startswith(prefix), f"Excluded prefix {prefix!r} found in output key {key!r}"

    @given(content=env_content(), prefixes=exclude_prefixes())
    def test_no_comments_or_blanks(self, content, prefixes):
        """Output contains no comment lines and no empty lines."""
        result = dokploy.filter_env(content, prefixes)
        for line in result.splitlines():
            assert line.strip() != "", "Blank line found in output"
            assert not line.strip().startswith("#"), "Comment found in output"

    @given(content=env_content(), prefixes=exclude_prefixes())
    def test_subset(self, content, prefixes):
        """Every line in output appears in input."""
        result = dokploy.filter_env(content, prefixes)
        input_lines = set(content.splitlines())
        for line in result.splitlines():
            assert line in input_lines, f"Output line {line!r} not found in input"


class TestResolveRefsProperties:
    @given(data=st.data())
    def test_known_refs_resolved(self, data):
        """All {name} refs where name exists in state are replaced with appName."""
        state = data.draw(state_dict(ref_safe=True))
        known_names = list(state["apps"].keys())
        assume(len(known_names) >= 1)

        name = data.draw(st.sampled_from(known_names))
        template = f"prefix-{{{name}}}-suffix"
        result = dokploy.resolve_refs(template, state)

        expected_app_name = state["apps"][name]["appName"]
        assert f"{{{name}}}" not in result
        assert expected_app_name in result

    @given(data=st.data())
    def test_unknown_refs_preserved(self, data):
        """Refs not in state remain unchanged."""
        state = data.draw(state_dict(ref_safe=True))
        known_names = set(state["apps"].keys())
        unknown = data.draw(ref_compatible_name.filter(lambda n: n not in known_names))
        template = f"value-{{{unknown}}}-end"
        result = dokploy.resolve_refs(template, state)
        assert f"{{{unknown}}}" in result

    @given(data=st.data())
    def test_no_refs_unchanged(self, data):
        """Template without {...} returns unchanged."""
        state = data.draw(state_dict(ref_safe=True))
        template = data.draw(
            st.text(
                alphabet=st.characters(blacklist_characters="{}"),
                min_size=0,
                max_size=50,
            )
        )
        result = dokploy.resolve_refs(template, state)
        assert result == template

    @given(data=st.data())
    def test_mixed_known_and_unknown(self, data):
        """Known refs resolved, unknown refs preserved in the same template."""
        state = data.draw(state_dict(ref_safe=True))
        known_names = list(state["apps"].keys())
        assume(len(known_names) >= 1)

        known = data.draw(st.sampled_from(known_names))
        unknown = data.draw(ref_compatible_name.filter(lambda n: n not in set(known_names)))

        template = f"{{{known}}}-{{{unknown}}}"
        result = dokploy.resolve_refs(template, state)

        assert state["apps"][known]["appName"] in result
        assert f"{{{unknown}}}" in result


class TestMergeEnvOverridesProperties:
    @given(cfg=dokploy_config())
    def test_original_config_immutable(self, cfg):
        """merge_env_overrides never mutates the original config."""
        original = copy.deepcopy(cfg)
        dokploy.merge_env_overrides(cfg, "prod")
        assert cfg == original

    @given(data=st.data())
    def test_override_wins(self, data):
        """For any key in both base and override, the override value appears in result."""
        cfg = data.draw(dokploy_config())
        app_names = [a["name"] for a in cfg["apps"]]
        assume(len(app_names) >= 1)

        target_name = data.draw(st.sampled_from(app_names))
        env_name = "testenv"
        override_cmd = "echo overridden"

        cfg["environments"] = {env_name: {"apps": {target_name: {"command": override_cmd}}}}
        merged = dokploy.merge_env_overrides(cfg, env_name)
        target_app = next(a for a in merged["apps"] if a["name"] == target_name)
        assert target_app["command"] == override_cmd

    @given(data=st.data())
    def test_base_preserved_for_non_overridden_keys(self, data):
        """Keys not in override retain their base values."""
        cfg = data.draw(dokploy_config())
        app_names = [a["name"] for a in cfg["apps"]]
        assume(len(app_names) >= 1)

        target_name = data.draw(st.sampled_from(app_names))
        target_app_original = next(a for a in cfg["apps"] if a["name"] == target_name)
        original_source = target_app_original["source"]

        # Override only "command", not "source"
        cfg["environments"] = {"testenv": {"apps": {target_name: {"command": "echo x"}}}}
        merged = dokploy.merge_env_overrides(cfg, "testenv")
        target_app = next(a for a in merged["apps"] if a["name"] == target_name)
        assert target_app["source"] == original_source

    @given(cfg=dokploy_config())
    def test_missing_env_is_identity(self, cfg):
        """Merging a nonexistent env returns base config minus environments key."""
        merged = dokploy.merge_env_overrides(cfg, "nonexistent_env")
        assert "environments" not in merged
        assert merged["project"] == cfg["project"]
        assert len(merged["apps"]) == len(cfg["apps"])

    @given(cfg=dokploy_config())
    def test_environments_key_removed(self, cfg):
        """Merged result never contains the 'environments' key."""
        merged = dokploy.merge_env_overrides(cfg, "prod")
        assert "environments" not in merged


class TestValidateConfigProperties:
    @given(cfg=dokploy_config())
    def test_valid_configs_do_not_crash(self, cfg):
        """Configs generated by dokploy_config() are internally consistent."""
        dokploy.validate_config(cfg)

    @given(data=st.data())
    def test_invalid_deploy_order_always_caught(self, data):
        """deploy_order referencing a nonexistent app always raises SystemExit."""
        cfg = data.draw(dokploy_config())
        app_names = {a["name"] for a in cfg["apps"]}
        bogus = data.draw(app_name.filter(lambda n: n not in app_names))

        cfg["project"]["deploy_order"] = [[bogus]]
        with pytest.raises(SystemExit):
            dokploy.validate_config(cfg)

    @given(data=st.data())
    def test_invalid_env_targets_always_caught(self, data):
        """env_targets referencing a nonexistent app always raises SystemExit."""
        cfg = data.draw(dokploy_config())
        app_names = {a["name"] for a in cfg["apps"]}
        bogus = data.draw(app_name.filter(lambda n: n not in app_names))

        cfg["project"]["env_targets"] = [bogus]
        with pytest.raises(SystemExit):
            dokploy.validate_config(cfg)


class TestValidateEnvReferencesProperties:
    @given(cfg=dokploy_config())
    def test_valid_env_refs_do_not_crash(self, cfg):
        """Configs generated by dokploy_config() have only valid env references."""
        dokploy.validate_env_references(cfg)

    @given(data=st.data())
    def test_invalid_env_app_ref_always_caught(self, data):
        """Environment app overrides referencing a nonexistent app raise SystemExit."""
        cfg = data.draw(dokploy_config())
        app_names = {a["name"] for a in cfg["apps"]}
        bogus = data.draw(app_name.filter(lambda n: n not in app_names))

        cfg["environments"] = {"prod": {"apps": {bogus: {"command": "echo x"}}}}
        with pytest.raises(SystemExit):
            dokploy.validate_env_references(cfg)


class TestPipelineProperties:
    @given(cfg=dokploy_config())
    def test_yaml_round_trip_stability(self, cfg):
        """YAML dump + load of a valid config produces an equivalent config."""
        dumped = yaml.dump(cfg)
        reloaded = yaml.safe_load(dumped)
        assert reloaded == cfg

    @given(data=st.data())
    def test_merge_then_validate_succeeds(self, data):
        """A valid config merged with a valid env still passes validation."""
        cfg = data.draw(dokploy_config())
        env_name = data.draw(st.sampled_from(["prod", "dev", "staging", "nonexistent"]))
        merged = dokploy.merge_env_overrides(cfg, env_name)
        # The merged config should still be valid
        dokploy.validate_config(merged)

    @given(cfg=dokploy_config())
    def test_yaml_round_trip_then_validate(self, cfg):
        """Config survives YAML serialization and still passes validation."""
        dumped = yaml.dump(cfg)
        reloaded = yaml.safe_load(dumped)
        dokploy.validate_config(reloaded)

    @given(cfg=dokploy_config())
    def test_merge_then_env_refs_valid(self, cfg):
        """validate_env_references passes on a generated config before merge."""
        dokploy.validate_env_references(cfg)
