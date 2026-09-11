"""Shared backend for hosts wired through one awiki-owned file (pi, OpenCode).

Both hosts auto-load every file in a directory, so "installing" is writing one
marker-tagged file and "uninstalling" is deleting it. The rules that keep this
safe against a user's real extension/plugin dir live here once: atomic writes,
byte-identical reinstall, and never overwriting or deleting a file that lacks
the awiki marker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ManagedFileHook:
    host: str                     # human label, e.g. "pi"
    event: str                    # host event the file subscribes to
    command: str                  # the awiki command it runs
    marker: str                   # substring proving awiki wrote the file
    source: str                   # full file contents
    default_path: Callable[[], Path]

    def _path(self, config_path: Path | None) -> Path:
        return config_path or self.default_path()

    def is_managed(self, path: Path) -> bool:
        try:
            return self.marker in path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False

    def _context_noop(self, verb: str) -> str:
        return (
            f"{self.host} has no auto-context hook (no prompt-submit event to attach "
            f"`awiki context` to); nothing to {verb} for --only context. The startup "
            f"sweep is the {self.host} hook: omit --only or pass --only sweep."
        )

    def install(self, config_path: Path | None = None, only: str | None = None) -> str:
        if only == "context":
            return self._context_noop("install")
        path = self._path(config_path)
        if path.exists():
            if not self.is_managed(path):
                raise ValueError(
                    f"Refusing to overwrite {path}: it is not awiki-managed (no marker). "
                    "Move it aside or pass --config-path."
                )
            if path.read_text(encoding="utf-8") == self.source:
                return f"Hook already installed at {path}."
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.source, encoding="utf-8")
        os.replace(tmp, path)
        return f"Installed `{self.command}` on {self.event} into {path}."

    def uninstall(self, config_path: Path | None = None, only: str | None = None) -> str:
        if only == "context":
            return self._context_noop("remove")
        path = self._path(config_path)
        if not path.exists():
            return f"Nothing to uninstall: {path} does not exist."
        if not self.is_managed(path):
            return f"Left {path} in place: it is not awiki-managed (no marker)."
        path.unlink()
        return f"Uninstalled {path}."

    def status(self, config_path: Path | None = None) -> str:
        path = self._path(config_path)
        line = f"  sweep    {self.event} `{self.command}`: "
        if path.exists() and self.is_managed(path):
            state = "installed"
        elif path.exists():
            state = "not installed (a file exists there but is not awiki-managed)"
        else:
            state = "not installed"
        return f"{self.host} hooks ({path}):\n{line}{state}"
