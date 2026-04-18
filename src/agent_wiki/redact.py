"""Minimal regex redactor for conversation bodies.

Applied unconditionally inside ``ingest_conversation``. Cheap and additive:
patterns only strip things that look sensitive. Tune via wiki.yaml:

    redaction:
      enabled: true
      username: rob          # replace the literal local username
      patterns:              # extra user regexes, each replaces with [REDACTED]
        - "sk-[A-Za-z0-9]{20,}"
"""
from __future__ import annotations

import getpass
import re
from dataclasses import dataclass, field
from typing import Any

# Reasonable defaults — emails and common token-ish prefixes.
_DEFAULT_PATTERNS: tuple[str, ...] = (
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",                 # emails
    r"\bsk-[A-Za-z0-9_-]{20,}\b",                                       # OpenAI-ish
    r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",                                   # Anthropic-ish
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",                                  # GitHub tokens
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",                                # Slack tokens
    r"AIza[0-9A-Za-z_-]{30,}",                                          # Google API keys
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
)


@dataclass
class Redactor:
    enabled: bool = True
    username: str | None = None
    patterns: list[re.Pattern] = field(default_factory=list)

    def redact(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        out = text
        for pat in self.patterns:
            out = pat.sub("[REDACTED]", out)
        if self.username:
            # word-boundary replace of the username
            out = re.sub(rf"\b{re.escape(self.username)}\b", "[USER]", out)
        return out


def make_redactor(config: dict[str, Any]) -> Redactor:
    enabled = config.get("enabled", True)
    if not enabled:
        return Redactor(enabled=False)

    username = config.get("username")
    if username is None:
        try:
            username = getpass.getuser()
        except Exception:
            username = None

    patterns: list[re.Pattern] = [re.compile(p) for p in _DEFAULT_PATTERNS]
    for extra in config.get("patterns") or []:
        try:
            patterns.append(re.compile(extra))
        except re.error:
            continue

    return Redactor(enabled=True, username=username or None, patterns=patterns)
