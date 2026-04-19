import json
from pathlib import Path

from click.testing import CliRunner

from agent_wiki.cli import cli


def test_tmp_settings_fixture_creates_empty_settings(tmp_settings):
    assert tmp_settings.exists()
    assert json.loads(tmp_settings.read_text()) == {}


def test_context_emits_additional_context_when_hits(tmp_config, tmp_vault):
    page = tmp_vault / "research" / "ingest-pipeline.md"
    page.write_text(
        "---\ntitle: Ingest Pipeline\ntopic: research\n---\n\n"
        "# Ingest Pipeline\n\nThe ingest pipeline handles codex sessions.\n"
    )
    runner = CliRunner()
    stdin = json.dumps({
        "prompt": "how do I configure the ingest pipeline for codex sessions",
    })
    result = runner.invoke(cli, ["context"], input=stdin)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "hookSpecificOutput" in payload
    assert "Ingest Pipeline" in payload["hookSpecificOutput"]["additionalContext"]


def test_context_silent_on_no_hits(tmp_config, tmp_vault):
    runner = CliRunner()
    stdin = json.dumps({"prompt": "tell me about quantum tunneling physics"})
    result = runner.invoke(cli, ["context"], input=stdin)
    assert result.exit_code == 0
    assert result.output == ""


def test_context_silent_on_slash_command(tmp_config, tmp_vault):
    runner = CliRunner()
    result = runner.invoke(cli, ["context"], input=json.dumps({"prompt": "/help"}))
    assert result.exit_code == 0
    assert result.output == ""


def test_context_silent_on_malformed_stdin(tmp_config, tmp_vault):
    runner = CliRunner()
    result = runner.invoke(cli, ["context"], input="not-json")
    assert result.exit_code == 0
    assert result.output == ""


def test_context_silent_on_missing_prompt_field(tmp_config, tmp_vault):
    runner = CliRunner()
    result = runner.invoke(cli, ["context"], input=json.dumps({"other": "x"}))
    assert result.exit_code == 0
    assert result.output == ""


def test_context_plain_output_format(tmp_config, tmp_vault):
    page = tmp_vault / "research" / "x.md"
    page.write_text(
        "---\ntitle: X\ntopic: research\n---\n\n# X\n\ningest pipeline codex\n"
    )
    runner = CliRunner()
    stdin = json.dumps({"prompt": "how do I configure the ingest pipeline for codex"})
    result = runner.invoke(cli, ["context", "--output-format", "plain"], input=stdin)
    assert result.exit_code == 0
    # Plain: bare block on stdout, no JSON envelope.
    assert "agent-wiki:" in result.output
    assert "hookSpecificOutput" not in result.output


def test_context_silent_when_no_vault_configured(tmp_path, monkeypatch):
    # No tmp_config fixture — no vault configured.
    monkeypatch.setenv("AGENT_WIKI_CONFIG_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["context"], input=json.dumps({"prompt": "some long enough prompt here"}))
    assert result.exit_code == 0
    assert result.output == ""


from agent_wiki.hooks import BACKENDS, get_backend


def test_backends_dict_contains_claude_and_manual():
    assert "claude" in BACKENDS
    assert "manual" in BACKENDS


def test_get_backend_raises_on_unknown_agent():
    import pytest
    with pytest.raises(KeyError):
        get_backend("opencode")


def test_manual_backend_has_install_function():
    backend = get_backend("manual")
    assert callable(backend["install"])
