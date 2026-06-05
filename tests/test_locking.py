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
