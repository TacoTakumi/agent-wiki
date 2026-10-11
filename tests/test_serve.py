import pytest


def test_serve_builds_app(tmp_config):
    from agent_wiki.config import get_vault_path
    from agent_wiki.server_config import load_server_config
    from agent_wiki.server.app import create_app
    from fastapi.testclient import TestClient
    app = create_app(get_vault_path(), load_server_config())
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/v1/status").status_code == 401


# --- one vault per serve instance ----------------------------------------------

def test_serve_instances_from_one_config_serve_different_vaults(
        tmp_path, monkeypatch):
    """Vault selection is per-instance via the --vault/AWIKI_VAULT override;
    two apps built from one config dir serve different vaults."""
    import yaml
    from fastapi.testclient import TestClient
    from agent_wiki.config import get_vault_path
    from agent_wiki.server.app import create_app
    from conftest import make_vault, seed_server_tokens

    work = make_vault(tmp_path / "work-vault")
    personal = make_vault(tmp_path / "personal-vault")
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({
        "default_vault": "work",
        "vaults": {"work": {"path": str(work)},
                   "personal": {"path": str(personal)}},
    }))
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(config_dir))
    seed_server_tokens()
    cfg = {"bind": "127.0.0.1", "port": 8731}

    # Default vault instance.
    app_default = create_app(get_vault_path(), cfg)
    # Named-vault instance (as `awiki --vault personal serve --port 8732`).
    monkeypatch.setenv("AWIKI_VAULT", "personal")
    app_named = create_app(get_vault_path(), cfg)
    monkeypatch.delenv("AWIKI_VAULT")

    headers = {"Authorization": "Bearer reader-tok"}
    st_default = TestClient(app_default).get("/v1/status", headers=headers).json()
    st_named = TestClient(app_named).get("/v1/status", headers=headers).json()
    assert st_default["vault"] == str(work)
    assert st_named["vault"] == str(personal)


def test_serve_route_set_is_vault_implicit_and_unchanged(tmp_vault):
    """The wire contract: endpoints stay vault-implicit with no vault
    selector, and the route set is pinned to the current release."""
    from agent_wiki.server.app import create_app

    app = create_app(tmp_vault, {"bind": "127.0.0.1", "port": 8731,
                                 "tokens": []})
    paths = sorted(app.openapi()["paths"].keys())
    assert paths == [
        "/v1/adapt", "/v1/context", "/v1/conversations", "/v1/doctor",
        "/v1/index", "/v1/ingest", "/v1/ingest_url", "/v1/lint", "/v1/log",
        "/v1/pages/{path}", "/v1/reingest", "/v1/search", "/v1/status",
        "/v1/sync",
    ]
    assert not any("vault" in p for p in paths)


# --- proxy sign-in settings and uvicorn's proxy headers -------------------------

@pytest.fixture
def run_serve(tmp_config, monkeypatch):
    """Run `awiki serve` with server.yaml set to ``settings`` and uvicorn.run
    stubbed; returns the CLI result and the recorded uvicorn calls."""
    import uvicorn
    import yaml
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    from agent_wiki.server_config import server_config_path

    def run(settings):
        calls = []
        monkeypatch.setattr(uvicorn, "run",
                            lambda *a, **k: calls.append((a, k)))
        if settings:
            server_config_path().write_text(yaml.dump(settings))
        result = CliRunner().invoke(cli, ["serve"])
        return result, calls
    return run


def test_serve_runs_uvicorn_with_proxy_headers_off(run_serve):
    result, calls = run_serve({})
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][1]["proxy_headers"] is False


def test_serve_with_proxy_on_and_defaults_starts(run_serve):
    result, calls = run_serve({"auth_proxy": True})
    assert result.exit_code == 0, result.output
    assert calls[0][1]["proxy_headers"] is False


def test_serve_with_proxy_off_still_refuses_a_wildcard_list(run_serve):
    # The list also decides whose X-Forwarded-Proto counts, so it is checked
    # with proxy sign-in off too.
    result, calls = run_serve({"auth_proxy": False,
                               "auth_trusted_proxies": ["*"]})
    assert result.exit_code != 0
    assert "auth_trusted_proxies" in result.output
    assert calls == []


@pytest.mark.parametrize("trusted, fault", [
    ([], "must list at least one"),
    (["*"], "'*' trusts every address"),
    (["0.0.0.0/0"], "'0.0.0.0/0' trusts every address"),
    (["::/0"], "'::/0' trusts every address"),
    (["proxy.example"], "'proxy.example' is not an address or a CIDR range"),
])
def test_serve_refuses_a_bad_trusted_list(run_serve, trusted, fault):
    result, calls = run_serve({"auth_proxy": True,
                               "auth_trusted_proxies": trusted})
    assert result.exit_code != 0
    assert "auth_trusted_proxies" in result.output
    assert fault in result.output
    assert calls == []


@pytest.mark.parametrize("header, fault", [
    ("", "must name a header"),
    ("X_WEBAUTH_USER", "underscore"),
])
def test_serve_refuses_a_bad_proxy_header(run_serve, header, fault):
    result, calls = run_serve({"auth_proxy": True, "auth_proxy_header": header})
    assert result.exit_code != 0
    assert fault in result.output
    assert calls == []


# --- startup warnings about the user store --------------------------------------

def _store():
    from agent_wiki.server.store import open_store
    return open_store()


def test_serve_on_an_empty_store_warns_nobody_can_sign_in(run_serve):
    result, calls = run_serve({})
    assert result.exit_code == 0, result.output
    assert "nobody can sign in" in result.output
    assert "awiki user add" in result.output
    assert len(calls) == 1
    with _store() as store:
        assert store.list_users() == []


def test_serve_with_users_does_not_warn_about_an_empty_store(run_serve):
    from agent_wiki.server import accounts
    with _store() as store:
        accounts.add_user(store, "alice", None)
        accounts.set_grant(store, "alice", "*", "reader")
    result, calls = run_serve({})
    assert result.exit_code == 0, result.output
    assert "awiki user add" not in result.output
    assert "WARNING" not in result.output


def test_serve_warns_for_a_grant_on_a_vault_not_in_the_registry(run_serve):
    from agent_wiki.server import accounts
    with _store() as store:
        accounts.add_user(store, "alice", None)
        accounts.set_grant(store, "alice", "gone", "reader")
    result, calls = run_serve({})
    assert result.exit_code == 0, result.output
    warnings = [line for line in result.output.splitlines() if "'gone'" in line]
    assert len(warnings) == 1
    assert "alice" in warnings[0]
    assert "grant" in warnings[0]
    assert len(calls) == 1


def test_serve_warns_for_a_token_scope_on_a_vault_not_in_the_registry(run_serve):
    from agent_wiki.server import accounts
    from agent_wiki.server.store import Token
    with _store() as store:
        accounts.add_user(store, "bob", None)
        accounts.set_grant(store, "bob", "*", "reader")
        store.add_token(Token(
            owner="bob", name="laptop", hash=accounts.hash_secret("s3cret"),
            last4="cret", scope={"gone": "reader"},
            created="2026-01-01T00:00:00Z"))
    result, calls = run_serve({})
    assert result.exit_code == 0, result.output
    warnings = [line for line in result.output.splitlines() if "'gone'" in line]
    assert len(warnings) == 1
    assert "bob" in warnings[0]
    assert "token laptop" in warnings[0]


def test_serve_counts_local_as_known_under_a_bare_path_override(
        run_serve, tmp_path, monkeypatch):
    # A bare-path override is served as 'local'; a grant on it is live.
    from conftest import make_vault
    from agent_wiki.server import accounts
    bare = make_vault(tmp_path / "bare")
    monkeypatch.setenv("AWIKI_VAULT", str(bare))
    with _store() as store:
        accounts.add_user(store, "alice", None)
        accounts.set_grant(store, "alice", "local", "reader")
    result, calls = run_serve({})
    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.output
    assert len(calls) == 1


def test_serve_leaves_an_invalid_vault_out_and_serves_the_rest(
        tmp_config, tmp_vault, monkeypatch):
    import uvicorn
    import yaml
    from click.testing import CliRunner
    from agent_wiki.cli import cli
    from agent_wiki.config import get_config_dir
    from conftest import signed_in_client
    config_file = get_config_dir() / "config.yaml"
    config_file.write_text(yaml.dump({"vaults": {
        "ok": {"path": str(tmp_vault)}, "a/b": {"path": str(tmp_vault)}}}))
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append(a))
    result = CliRunner().invoke(cli, ["serve", "--viewer"])
    assert result.exit_code == 0, result.output
    warning = (f"warning: vault 'a/b' in {config_file} is not a valid name; "
               "use lowercase letters, digits and hyphens (e.g. 'ab'). Skipped.")
    assert result.output.splitlines().count(warning) == 1
    assert len(calls) == 1
    html = signed_in_client(calls[0][0]).get("/").text
    assert 'href="/vaults/ok/"' in html
    assert "a/b" not in html
