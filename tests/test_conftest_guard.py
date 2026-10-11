import os


def test_guard_clears_a_vault_override_from_the_shell():
    # Run with AWIKI_VAULT set in the shell to prove the autouse guard drops it.
    assert "AWIKI_VAULT" not in os.environ
