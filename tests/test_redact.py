from agent_wiki.redact import Redactor, make_redactor


def test_redactor_strips_emails():
    r = make_redactor({})
    out = r.redact("contact me at alice@example.com please")
    assert "alice@example.com" not in out
    assert "[REDACTED]" in out


def test_redactor_strips_anthropic_token():
    r = make_redactor({})
    sample = "here is a token sk-ant-api01-ABCDEFGHIJ1234567890abcdef extra"
    out = r.redact(sample)
    assert "sk-ant-api01-ABCDEFGHIJ1234567890abcdef" not in out
    assert "[REDACTED]" in out


def test_redactor_strips_github_token():
    r = make_redactor({})
    out = r.redact("my pat is ghp_AAAAAAAAAAAAAAAAAAAA!!")
    assert "[REDACTED]" in out
    assert "ghp_AAAAAAAAAAAAAAAAAAAA" not in out


def test_redactor_replaces_username():
    r = make_redactor({"username": "rob"})
    out = r.redact("hello rob how are you, rob's fine")
    # Word-bounded replace: 'rob' standalone replaced, 'rob's' replaced too (re.sub behavior)
    assert "[USER]" in out
    # Substring inside 'robot' must NOT be replaced
    out2 = r.redact("robot motors")
    assert "[USER]" not in out2


def test_redactor_custom_patterns():
    r = make_redactor({"patterns": [r"SECRET_[A-Z]+"]})
    out = r.redact("my SECRET_TOKEN is ...")
    assert "[REDACTED]" in out
    assert "SECRET_TOKEN" not in out


def test_redactor_disabled():
    r = make_redactor({"enabled": False})
    assert r.redact("alice@example.com") == "alice@example.com"


def test_redactor_ignores_invalid_pattern():
    r = make_redactor({"patterns": [r"[invalid"]})
    # Invalid pattern quietly dropped; default patterns still work
    assert "[REDACTED]" in r.redact("alice@example.com")


def test_redactor_strips_private_key_block():
    r = make_redactor({})
    sample = (
        "before\n-----BEGIN RSA PRIVATE KEY-----\nABCDEF\n-----END RSA PRIVATE KEY-----\nafter"
    )
    out = r.redact(sample)
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert "[REDACTED]" in out
    assert "after" in out
