from pathlib import Path


def resolve_in_vault(vault_path: Path, user_path: str) -> Path:
    """Resolve a user-supplied path against the vault root, confined to it.

    Joins ``user_path`` onto the resolved vault root and resolves the result
    (following symlinks). Raises ``ValueError`` if the resolved path escapes
    the vault — via ``..``, an absolute path landing outside, or an escaping
    symlink. Returns the resolved absolute path (which may not exist).
    """
    vault_root = vault_path.resolve()
    target = (vault_root / user_path).resolve()
    if target != vault_root and vault_root not in target.parents:
        raise ValueError(f"path is outside the vault: {user_path}")
    return target


def read_vault_file(vault_path: Path, user_path: str) -> str:
    """Return the verbatim text of a vault file given its vault-relative path.

    Confines the path to the vault (see ``resolve_in_vault``). Raises:
      - ``ValueError`` if the path escapes the vault, or the file is not
        valid UTF-8 (e.g. a PDF under ``raw/``).
      - ``FileNotFoundError`` if the path does not exist or is a directory.
    """
    target = resolve_in_vault(vault_path, user_path)
    if not target.is_file():
        raise FileNotFoundError(f"no such page: {user_path}")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"cannot display binary file: {user_path}")


def read_vault_bytes(vault_path: Path, user_path: str) -> bytes:
    """Return the raw bytes of a vault file given its vault-relative path.

    Confines the path to the vault (see ``resolve_in_vault``). Raises
    ``FileNotFoundError`` if missing/a directory, ``ValueError`` if the path
    escapes the vault. Unlike ``read_vault_file`` it does NOT refuse binary
    content — the caller decides how to serve it.
    """
    target = resolve_in_vault(vault_path, user_path)
    if not target.is_file():
        raise FileNotFoundError(f"no such page: {user_path}")
    return target.read_bytes()
