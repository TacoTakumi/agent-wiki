"""Claude Code hook backend — real implementation in Task 10/11."""

from __future__ import annotations

from pathlib import Path


def install(config_path: Path | None = None) -> str:
    raise NotImplementedError("claude backend install: implemented in Task 10")


def uninstall(config_path: Path | None = None) -> str:
    raise NotImplementedError("claude backend uninstall: implemented in Task 10")


def status(config_path: Path | None = None) -> str:
    raise NotImplementedError("claude backend status: implemented in Task 11")
