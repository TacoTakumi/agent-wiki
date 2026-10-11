"""Password hashing for awiki serve: scrypt with stored parameters."""
from __future__ import annotations

import secrets

from agent_wiki.server.accounts import hash_password, needs_rehash, verify_password


def test_hash_names_current_parameters():
    stored = hash_password("correct horse")
    parts = stored.split("$")
    assert len(parts) == 6
    assert parts[:4] == ["scrypt", "16384", "8", "5"]
    assert len(bytes.fromhex(parts[4])) == 16
    assert len(bytes.fromhex(parts[5])) == 32


def test_salt_is_fresh_each_time():
    assert hash_password("same") != hash_password("same")


def test_right_password_verifies():
    stored = hash_password("correct horse")
    assert verify_password("correct horse", stored) is True


def test_wrong_password_does_not_verify():
    stored = hash_password("correct horse")
    assert verify_password("battery staple", stored) is False


def test_current_hash_needs_no_rehash():
    assert needs_rehash(hash_password("pw")) is False


def test_weaker_hash_needs_rehash_but_still_verifies():
    stored = hash_password("pw", n=1024)
    assert stored.split("$")[1] == "1024"
    assert needs_rehash(stored) is True
    assert verify_password("pw", stored) is True


def test_hash_over_memory_cap_verifies_false():
    # N=2^20 with r=8 asks for about 1 GiB, over the 256 MiB cap.
    salt = secrets.token_bytes(16).hex()
    digest = secrets.token_bytes(32).hex()
    stored = f"scrypt${2 ** 20}$8$5${salt}${digest}"
    assert verify_password("pw", stored) is False


def test_malformed_stored_strings_verify_false_and_need_rehash():
    for stored in ["", "plain", "scrypt$abc$8$5$00$00", "scrypt$1000$8$5$00$00",
                   "bcrypt$16384$8$5$00$00", "scrypt$16384$8$5$zz$00", None]:
        assert verify_password("pw", stored) is False
        assert needs_rehash(stored) is True
