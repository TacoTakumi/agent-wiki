"""Summarizers: turn a Conversation into a wiki-page-ready summary block.

Three backends:

- ``NoneSummarizer`` — returns None. Page body becomes a link to the raw
  transcript. Default; zero external dependencies.
- ``ClaudePSummarizer`` — shells out to ``claude -p`` using the user's
  subscription credentials. No API key required.
- ``LocalOpenAISummarizer`` — POSTs to a local OpenAI-compatible endpoint
  (e.g. llama.cpp server).

All are selectable from ``wiki.yaml``:

    summarizer:
      type: none         # none | claude-p | local-openai
      claude_p:
        args: ["-p", "--permission-mode=read-only"]
      local_openai:
        base_url: http://127.0.0.1:8080/v1
        model: ""
        max_tokens: 600
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from agent_wiki.conversation import Conversation


SYSTEM_PROMPT = (
    "You are summarizing a software engineering conversation so a future reader "
    "can recall what happened without re-reading the transcript. Produce "
    "markdown with these sections (skip a section if truly empty):\n\n"
    "## Context\n"
    "## Decisions\n"
    "## Key Exchanges\n"
    "## Open Threads\n\n"
    "Be specific: mention filenames, commands, error messages. Omit pleasantries. "
    "No preamble or sign-off."
)


class Summarizer(ABC):
    @abstractmethod
    def summarize(self, conv: Conversation) -> str | None:
        """Return a summary markdown block, or None to skip summarization."""


class NoneSummarizer(Summarizer):
    def summarize(self, conv: Conversation) -> str | None:
        return None


class ClaudePSummarizer(Summarizer):
    def __init__(self, args: list[str] | None = None, timeout: int = 120) -> None:
        self.args = args or ["-p"]
        self.timeout = timeout

    def summarize(self, conv: Conversation) -> str | None:
        prompt = self._prompt(conv)
        try:
            proc = subprocess.run(
                ["claude", *self.args],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        out = proc.stdout.strip()
        return out or None

    @staticmethod
    def _prompt(conv: Conversation) -> str:
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"---\nTitle: {conv.title}\nAgent: {conv.agent}\n"
            f"Project: {conv.project or '-'}\n---\n\n{conv.body}"
        )


class LocalOpenAISummarizer(Summarizer):
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080/v1",
        model: str = "",
        max_tokens: int = 600,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout

    def summarize(self, conv: Conversation) -> str | None:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_msg(conv)},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

        try:
            obj = json.loads(body)
            choice = obj["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError):
            return None
        content = content.strip()
        return content or None

    @staticmethod
    def _user_msg(conv: Conversation) -> str:
        return (
            f"Title: {conv.title}\nAgent: {conv.agent}\n"
            f"Project: {conv.project or '-'}\n\n{conv.body}"
        )


def make_summarizer(config: dict[str, Any]) -> Summarizer:
    """Build a Summarizer from a wiki.yaml ``summarizer:`` section."""
    kind = (config.get("type") or "none").lower()
    if kind in ("none", "off", "disabled"):
        return NoneSummarizer()
    if kind in ("claude-p", "claude_p", "claude"):
        sub = config.get("claude_p") or config.get("claude-p") or {}
        return ClaudePSummarizer(args=sub.get("args"))
    if kind in ("local-openai", "local_openai", "openai"):
        sub = config.get("local_openai") or config.get("local-openai") or {}
        return LocalOpenAISummarizer(
            base_url=sub.get("base_url", "http://127.0.0.1:8080/v1"),
            model=sub.get("model", ""),
            max_tokens=int(sub.get("max_tokens", 600)),
        )
    return NoneSummarizer()
