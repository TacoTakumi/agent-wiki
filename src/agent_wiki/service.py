"""VaultService facade: one method per command, returns plain data, never prints.

`LocalVaultService` is the in-process facade used by both the CLI (local vaults)
and the FastAPI server. `RemoteVaultService` (see remote.py) is the HTTP-backed
drop-in the CLI uses for remote vaults. Both honor the same contract.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from agent_wiki.context import run_context
from agent_wiki.lint import lint_vault
from agent_wiki.log import read_log
from agent_wiki.show import read_vault_bytes, read_vault_file


class VaultService(ABC):
    """The command surface shared by the local facade and the remote client."""

    @abstractmethod
    def show(self, rel: str) -> str: ...

    @abstractmethod
    def read_bytes(self, rel: str) -> bytes: ...

    @abstractmethod
    def log(self, last: int | None = None) -> list[str]: ...

    @abstractmethod
    def lint(self) -> list[dict]: ...

    @abstractmethod
    def context(self, prompt: str) -> str: ...


class LocalVaultService(VaultService):
    def __init__(self, vault_path: Path):
        self.vault_path = vault_path

    # --- reads (no lock) ---
    def show(self, rel: str) -> str:
        return read_vault_file(self.vault_path, rel)

    def read_bytes(self, rel: str) -> bytes:
        return read_vault_bytes(self.vault_path, rel)

    def log(self, last: int | None = None) -> list[str]:
        return read_log(self.vault_path, last=last)

    def lint(self) -> list[dict]:
        return lint_vault(self.vault_path)

    def context(self, prompt: str) -> str:
        return run_context(prompt, self.vault_path) or ""
