"""Out-of-tree advisory file locks for serializing vault mutations.

Locks live in a per-vault state dir OUTSIDE the vault md tree (runtime state,
never synced). Acquisition blocks up to a timeout by polling a non-blocking
``flock`` — this gives a portable timeout that works inside a thread pool
(unlike SIGALRM). The server runs mutating ops in a thread pool, so a waiting
request simply holds its HTTP response open; the remote client needs no retry
logic. On timeout we raise ``TimeoutError`` (mapped to HTTP 503 at the edge).
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_TIMEOUT = 10.0
_POLL_INTERVAL = 0.05


def _state_dir() -> Path:
    override = os.environ.get("AGENT_WIKI_STATE_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "agent-wiki"


def _vault_lock_dir(vault_path: Path) -> Path:
    digest = hashlib.sha256(str(Path(vault_path).resolve()).encode()).hexdigest()[:16]
    return _state_dir() / "locks" / digest


def lock_path(vault_path: Path, name: str) -> Path:
    d = _vault_lock_dir(vault_path)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.lock"


@contextmanager
def file_lock(vault_path: Path, name: str, timeout: float = DEFAULT_TIMEOUT):
    """Hold an exclusive advisory lock named ``name`` for ``vault_path``.

    Blocks up to ``timeout`` seconds, then raises ``TimeoutError``.
    """
    path = lock_path(vault_path, name)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire '{name}' lock for {vault_path} "
                        f"within {timeout}s"
                    )
                time.sleep(_POLL_INTERVAL)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
