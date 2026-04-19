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
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
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


from agent_wiki.hooks import claude as claude_backend


def test_claude_install_writes_hook_into_empty_settings(tmp_settings):
    claude_backend.install(config_path=tmp_settings)
    data = json.loads(tmp_settings.read_text())
    events = data["hooks"]["UserPromptSubmit"]
    assert len(events) == 1
    hooks = events[0]["hooks"]
    assert any(h["command"] == "awiki context" for h in hooks)


def test_claude_install_preserves_unrelated_settings(tmp_settings):
    tmp_settings.write_text(json.dumps({
        "model": "claude-opus-4-7",
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo pre"}]}]},
    }))
    claude_backend.install(config_path=tmp_settings)
    data = json.loads(tmp_settings.read_text())
    assert data["model"] == "claude-opus-4-7"
    assert "PreToolUse" in data["hooks"]
    assert "UserPromptSubmit" in data["hooks"]


def test_claude_install_is_idempotent(tmp_settings):
    claude_backend.install(config_path=tmp_settings)
    claude_backend.install(config_path=tmp_settings)
    data = json.loads(tmp_settings.read_text())
    hooks = data["hooks"]["UserPromptSubmit"][0]["hooks"]
    awiki_hooks = [h for h in hooks if h["command"] == "awiki context"]
    assert len(awiki_hooks) == 1


def test_claude_install_refuses_malformed_settings(tmp_settings):
    tmp_settings.write_text("{not valid json")
    import pytest
    with pytest.raises(ValueError):
        claude_backend.install(config_path=tmp_settings)
    # File unchanged.
    assert tmp_settings.read_text() == "{not valid json"


def test_claude_uninstall_removes_only_awiki_entry(tmp_settings):
    tmp_settings.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [{
                "matcher": "*",
                "hooks": [
                    {"type": "command", "command": "awiki context"},
                    {"type": "command", "command": "other-tool"},
                ],
            }],
        },
    }))
    claude_backend.uninstall(config_path=tmp_settings)
    data = json.loads(tmp_settings.read_text())
    hooks = data["hooks"]["UserPromptSubmit"][0]["hooks"]
    commands = [h["command"] for h in hooks]
    assert "awiki context" not in commands
    assert "other-tool" in commands


def test_claude_uninstall_is_noop_when_not_installed(tmp_settings):
    tmp_settings.write_text(json.dumps({"model": "claude-opus-4-7"}))
    # Should not raise.
    claude_backend.uninstall(config_path=tmp_settings)
    data = json.loads(tmp_settings.read_text())
    assert data == {"model": "claude-opus-4-7"}


def test_claude_uninstall_removes_empty_event_group(tmp_settings):
    """If removing awiki leaves an empty UserPromptSubmit, clean it up."""
    tmp_settings.write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": "awiki context"}],
            }],
        },
    }))
    claude_backend.uninstall(config_path=tmp_settings)
    data = json.loads(tmp_settings.read_text())
    # The event key should be gone entirely, or empty.
    assert not data.get("hooks", {}).get("UserPromptSubmit")


def test_claude_status_reports_installed(tmp_settings):
    claude_backend.install(config_path=tmp_settings)
    msg = claude_backend.status(config_path=tmp_settings)
    assert "installed" in msg.lower()


def test_claude_status_reports_not_installed(tmp_settings):
    msg = claude_backend.status(config_path=tmp_settings)
    assert "not installed" in msg.lower()


def test_cli_hook_install_claude_writes_settings(tmp_settings):
    runner = CliRunner()
    result = runner.invoke(cli, [
        "hook", "install",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(tmp_settings.read_text())
    hooks = data["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert any(h["command"] == "awiki context" for h in hooks)


def test_cli_hook_install_manual_prints_instructions():
    runner = CliRunner()
    result = runner.invoke(cli, ["hook", "install", "--agent", "manual"])
    assert result.exit_code == 0
    assert "awiki context" in result.output
    assert "UserPromptSubmit" in result.output


def test_cli_hook_install_unknown_agent_errors():
    runner = CliRunner()
    result = runner.invoke(cli, ["hook", "install", "--agent", "opencode"])
    assert result.exit_code != 0
    assert "manual" in result.output.lower()


def test_cli_hook_uninstall_removes_entry(tmp_settings):
    runner = CliRunner()
    runner.invoke(cli, [
        "hook", "install",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    result = runner.invoke(cli, [
        "hook", "uninstall",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    assert result.exit_code == 0
    data = json.loads(tmp_settings.read_text())
    assert not data.get("hooks", {}).get("UserPromptSubmit")


def test_cli_hook_status_reports_install_state(tmp_settings):
    runner = CliRunner()
    result = runner.invoke(cli, [
        "hook", "status",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    assert result.exit_code == 0
    assert "not installed" in result.output.lower()

    runner.invoke(cli, [
        "hook", "install",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    result = runner.invoke(cli, [
        "hook", "status",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    assert "installed" in result.output.lower()


def test_end_to_end_install_then_fire_hook(tmp_config, tmp_vault, tmp_settings):
    """Install the hook, seed a page, invoke `awiki context` with a matching
    prompt, and verify the structured output would land in Claude's context."""
    # Seed a wiki page.
    (tmp_vault / "research" / "ingest-pipeline.md").write_text(
        "---\ntitle: Ingest Pipeline\ntopic: research\n---\n\n"
        "# Ingest Pipeline\n\nThe ingest pipeline handles codex sessions.\n"
    )

    runner = CliRunner()

    # 1. Install the hook.
    install_result = runner.invoke(cli, [
        "hook", "install",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    assert install_result.exit_code == 0
    settings = json.loads(tmp_settings.read_text())
    hook_cmd = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert hook_cmd == "awiki context"

    # 2. Simulate Claude firing the hook with a matching prompt.
    stdin = json.dumps({
        "prompt": "how do I configure the ingest pipeline for codex sessions",
        "session_id": "test",
        "cwd": str(tmp_vault),
    })
    ctx_result = runner.invoke(cli, ["context"], input=stdin)
    assert ctx_result.exit_code == 0
    payload = json.loads(ctx_result.output)
    additional = payload["hookSpecificOutput"]["additionalContext"]
    assert "Ingest Pipeline" in additional
    assert "research/ingest-pipeline.md" in additional

    # 3. Uninstall and confirm the settings file is clean.
    runner.invoke(cli, [
        "hook", "uninstall",
        "--agent", "claude",
        "--config-path", str(tmp_settings),
    ])
    data = json.loads(tmp_settings.read_text())
    assert not data.get("hooks", {}).get("UserPromptSubmit")
