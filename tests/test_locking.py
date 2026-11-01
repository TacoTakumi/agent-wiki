import os
import threading
import time

import pytest

from agent_wiki.locking import file_lock, lock_path


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WIKI_STATE_DIR", str(tmp_path / "state"))


def test_lock_file_is_outside_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    p = lock_path(vault, "log")
    assert vault not in p.parents          # never inside the vault
    assert p.name == "log.lock"
    assert p.parent.exists()               # dir created on demand


def test_two_vaults_get_distinct_lock_dirs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    assert lock_path(a, "log").parent != lock_path(b, "log").parent


def test_lock_is_exclusive_and_blocks(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    order = []
    held = threading.Event()

    def first():
        with file_lock(vault, "log", timeout=5):
            held.set()
            time.sleep(0.3)
            order.append("first-release")

    def second():
        held.wait()
        with file_lock(vault, "log", timeout=5):
            order.append("second-acquire")

    t1 = threading.Thread(target=first); t2 = threading.Thread(target=second)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert order == ["first-release", "second-acquire"]


def test_timeout_raises(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    with file_lock(vault, "log", timeout=5):
        with pytest.raises(TimeoutError):
            # Same process, different fd: LOCK_NB contention -> timeout fast.
            with file_lock(vault, "log", timeout=0.2):
                pass


def test_zero_timeout_tries_once_and_returns_at_once(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    with file_lock(vault, "log", timeout=5):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with file_lock(vault, "log", timeout=0):
                pass
        assert time.monotonic() - started < 0.5


# --- Windows branch -------------------------------------------------------
#
# The lock primitive is chosen at import time from sys.platform. These tests
# reload the module as if on Windows, with a fake ``msvcrt`` whose
# ``locking`` mirrors the real one's contract (LK_NBLCK -> OSError EACCES on
# contention) on top of a real POSIX lock, so exclusivity is still real.

import errno
import importlib.util
import sys
import types

import agent_wiki.locking as locking_mod


def _fake_msvcrt():
    import fcntl

    m = types.ModuleType("msvcrt")
    m.LK_NBLCK = 2
    m.LK_UNLCK = 0
    m.calls = []

    def locking(fd, mode, nbytes):
        m.calls.append((mode, nbytes))
        if mode == m.LK_NBLCK:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise OSError(errno.EACCES, "Permission denied")
        elif mode == m.LK_UNLCK:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            raise AssertionError(f"unexpected mode {mode}")

    m.locking = locking
    return m


@pytest.fixture
def win_locking(monkeypatch):
    """A fresh copy of agent_wiki.locking imported as if on Windows.

    ``fcntl`` is made unimportable for the duration, so the import only
    succeeds if the Windows branch never touches it. The real module object
    stays untouched for the rest of the suite.
    """
    fake = _fake_msvcrt()                    # binds fcntl before we hide it
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setitem(sys.modules, "fcntl", None)   # import fcntl -> ImportError
    spec = importlib.util.spec_from_file_location(
        "agent_wiki._locking_win_test", locking_mod.__file__
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod, fake


def test_windows_branch_imports_without_fcntl(win_locking):
    mod, _ = win_locking
    assert "fcntl" not in mod.__dict__
    assert mod.msvcrt is sys.modules["msvcrt"]


def test_windows_branch_is_exclusive_and_times_out(tmp_path, win_locking):
    mod, fake = win_locking
    vault = tmp_path / "vault"; vault.mkdir()
    with mod.file_lock(vault, "log", timeout=5):
        with pytest.raises(TimeoutError):
            with mod.file_lock(vault, "log", timeout=0.2):
                pass
    # One non-blocking lock per attempt, one unlock on release of the outer.
    assert (fake.LK_NBLCK, 1) in fake.calls
    assert fake.calls[-1] == (fake.LK_UNLCK, 1)
