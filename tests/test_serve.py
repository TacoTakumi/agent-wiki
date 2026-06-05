def test_serve_builds_app(tmp_config):
    from agent_wiki.config import get_vault_path
    from agent_wiki.server_config import load_server_config
    from agent_wiki.server.app import create_app
    from fastapi.testclient import TestClient
    app = create_app(get_vault_path(), load_server_config())
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/v1/status").status_code == 401
