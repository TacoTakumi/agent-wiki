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
