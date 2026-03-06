"""Reusable Hypothesis strategies for dokploy_seed property tests."""

from hypothesis import strategies as st


app_name = st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True)

# resolve_refs uses \{(\w+)\} — only matches [a-zA-Z0-9_], not hyphens.
ref_compatible_name = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)

env_key = st.from_regex(r"[A-Z][A-Z0-9_]{0,20}", fullmatch=True)

env_value = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"), blacklist_characters="\n\r"),
    min_size=0,
    max_size=30,
)

env_line = st.builds(lambda k, v: f"{k}={v}", env_key, env_value)

comment_line = st.builds(lambda t: f"# {t}", st.text(min_size=0, max_size=30).filter(lambda s: "\n" not in s))

blank_line = st.just("")


@st.composite
def env_content(draw):
    """Generate realistic .env file content (KEY=value lines, comments, blanks)."""
    lines = draw(
        st.lists(
            st.one_of(env_line, comment_line, blank_line),
            min_size=0,
            max_size=20,
        )
    )
    return "\n".join(lines)


@st.composite
def exclude_prefixes(draw):
    """Generate a list of uppercase prefixes to exclude."""
    prefixes = draw(
        st.lists(
            st.from_regex(r"[A-Z][A-Z0-9_]{0,8}", fullmatch=True),
            min_size=0,
            max_size=5,
        )
    )
    return prefixes


@st.composite
def state_dict(draw, *, ref_safe=False):
    """Generate a state dict with apps mapping name -> {applicationId, appName}.

    Args:
        ref_safe: If True, use only \\w-compatible names (no hyphens) so that
                  resolve_refs' \\{(\\w+)\\} regex can match them.
    """
    name_strat = ref_compatible_name if ref_safe else app_name
    names = draw(st.lists(name_strat, min_size=1, max_size=5, unique=True))
    apps = {}
    for name in names:
        app_id = draw(st.from_regex(r"app-[a-z0-9]{6}", fullmatch=True))
        app_name_val = draw(st.from_regex(r"[a-z]+_[a-z0-9]{6}", fullmatch=True))
        apps[name] = {"applicationId": app_id, "appName": app_name_val}
    return {
        "projectId": draw(st.from_regex(r"proj-[a-z0-9]{6}", fullmatch=True)),
        "environmentId": draw(st.from_regex(r"env-[a-z0-9]{6}", fullmatch=True)),
        "apps": apps,
    }


@st.composite
def template_with_refs(draw, state):
    """Generate a template string containing {ref} placeholders from known state keys."""
    known_names = list(state["apps"].keys())
    unknown_names = draw(
        st.lists(
            app_name.filter(lambda n: n not in known_names),
            min_size=0,
            max_size=3,
        )
    )
    all_refs = known_names + unknown_names
    parts = []
    for ref in draw(st.permutations(all_refs)):
        prefix = draw(
            st.text(
                alphabet=st.characters(whitelist_categories=("L", "N"), blacklist_characters="{}"),
                min_size=0,
                max_size=10,
            )
        )
        parts.append(f"{prefix}{{{ref}}}")
    trailing = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), blacklist_characters="{}"),
            min_size=0,
            max_size=10,
        )
    )
    parts.append(trailing)
    return "".join(parts), set(known_names), set(unknown_names)


source_strategy = st.sampled_from(["docker", "github"])


@st.composite
def app_config(draw, name=None, source=None):
    """Generate a valid app config dict."""
    _name = name or draw(app_name)
    _source = source or draw(source_strategy)
    app = {"name": _name, "source": _source}
    if _source == "docker":
        app["dockerImage"] = draw(st.from_regex(r"[a-z]+:[a-z0-9.]+", fullmatch=True))
    if draw(st.booleans()):
        app["command"] = draw(st.text(min_size=1, max_size=50).filter(lambda s: "\n" not in s))
    return app


@st.composite
def dokploy_config(draw, *, with_github=False):
    """Generate a full valid dokploy config dict matching the JSON schema."""
    names = draw(st.lists(app_name, min_size=1, max_size=5, unique=True))

    has_github = with_github or draw(st.booleans())

    apps = []
    for name in names:
        source = "github" if has_github and draw(st.booleans()) else "docker"
        apps.append(draw(app_config(name=name, source=source)))

    # If any github-sourced apps exist, force has_github
    if any(a["source"] == "github" for a in apps):
        has_github = True

    deploy_order = draw(st.just([names]) | st.just([[n] for n in names]))
    env_targets = draw(st.just([]) | st.lists(st.sampled_from(names), min_size=0, max_size=len(names), unique=True))

    cfg = {
        "project": {
            "name": draw(st.text(min_size=1, max_size=20).filter(lambda s: "\n" not in s)),
            "description": draw(st.text(min_size=1, max_size=50).filter(lambda s: "\n" not in s)),
            "deploy_order": deploy_order,
            "env_targets": env_targets,
        },
        "apps": apps,
    }

    if has_github:
        cfg["github"] = {
            "owner": draw(st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True)),
            "repository": draw(st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True)),
            "branch": draw(st.sampled_from(["main", "develop", "staging"])),
        }

    # Optionally add environment overrides that reference valid app names
    if draw(st.booleans()):
        env_name = draw(st.sampled_from(["prod", "dev", "staging"]))
        app_overrides = {}
        for name in draw(st.lists(st.sampled_from(names), min_size=0, max_size=len(names), unique=True)):
            override = {}
            if draw(st.booleans()):
                override["command"] = "echo override"
            if draw(st.booleans()):
                override["dockerImage"] = "nginx:latest"
            if override:
                app_overrides[name] = override
        env_cfg = {}
        if app_overrides:
            env_cfg["apps"] = app_overrides
        if has_github and draw(st.booleans()):
            env_cfg["github"] = {"branch": draw(st.sampled_from(["main", "release", "develop"]))}
        if env_cfg:
            cfg["environments"] = {env_name: env_cfg}

    return cfg
