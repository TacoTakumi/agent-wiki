import yaml
import pytest

from agent_wiki.config import (
    TagVocabulary,
    parse_tag_vocabulary,
    load_tag_vocabulary,
    detect_vocabulary_conflicts,
)


def _write_wiki(vault, config):
    (vault / "wiki.yaml").write_text(yaml.dump(config))


# --- parsing -----------------------------------------------------------------


def test_block_parses_to_mode_and_mapping():
    vocab = parse_tag_vocabulary(
        {
            "tags": {
                "mode": "strict",
                "vocabulary": {"stt": ["asr", "speech-to-text"]},
            }
        }
    )
    assert vocab.mode == "strict"
    assert vocab.vocabulary == {"stt": ["asr", "speech-to-text"]}
    assert not vocab.is_off


def test_absent_block_is_off_and_empty():
    vocab = parse_tag_vocabulary({"topics": ["research"]})
    assert vocab.mode == "off"
    assert vocab.vocabulary == {}
    assert vocab.is_off


def test_empty_config_is_off():
    # A None / empty config (as load_vault_config can yield) must not error.
    assert parse_tag_vocabulary(None).is_off
    assert parse_tag_vocabulary({}).is_off


def test_block_present_mode_unset_defaults_to_warn():
    vocab = parse_tag_vocabulary({"tags": {"vocabulary": {"stt": ["asr"]}}})
    assert vocab.mode == "warn"
    assert not vocab.is_off


def test_mode_off_is_off_even_with_vocabulary():
    vocab = parse_tag_vocabulary(
        {"tags": {"mode": "off", "vocabulary": {"stt": ["asr"]}}}
    )
    assert vocab.mode == "off"
    assert vocab.is_off


def test_aliases_none_and_scalar_are_coerced_to_lists():
    vocab = parse_tag_vocabulary(
        {"tags": {"vocabulary": {"stt": None, "gpu": "tensor-split"}}}
    )
    assert vocab.vocabulary == {"stt": [], "gpu": ["tensor-split"]}


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        parse_tag_vocabulary({"tags": {"mode": "loud", "vocabulary": {}}})


# --- loading from a vault ----------------------------------------------------


def test_load_tag_vocabulary_from_vault(tmp_vault):
    config = yaml.safe_load((tmp_vault / "wiki.yaml").read_text())
    config["tags"] = {"mode": "warn", "vocabulary": {"stt": ["asr"]}}
    _write_wiki(tmp_vault, config)
    vocab = load_tag_vocabulary(tmp_vault)
    assert vocab.mode == "warn"
    assert vocab.vocabulary == {"stt": ["asr"]}


def test_load_tag_vocabulary_absent_block_is_off(tmp_vault):
    # tmp_vault's wiki.yaml carries no tags block.
    assert load_tag_vocabulary(tmp_vault).is_off


# --- conflict detection ------------------------------------------------------


def test_clean_vocabulary_has_no_conflicts():
    vocab = parse_tag_vocabulary(
        {"tags": {"vocabulary": {"stt": ["asr"], "gpu": ["tensor-split"]}}}
    )
    assert detect_vocabulary_conflicts(vocab) == []


def test_alias_bound_to_two_preferred_is_a_conflict():
    vocab = parse_tag_vocabulary(
        {"tags": {"vocabulary": {"stt": ["asr"], "speech": ["asr"]}}}
    )
    conflicts = detect_vocabulary_conflicts(vocab)
    assert conflicts
    c = conflicts[0]
    assert c.token == "asr"
    assert set(c.preferred) == {"stt", "speech"}
    assert "asr" in c.message


def test_string_that_is_both_preferred_and_alias_is_a_conflict():
    vocab = parse_tag_vocabulary(
        {"tags": {"vocabulary": {"stt": ["asr"], "speech": ["stt"]}}}
    )
    conflicts = detect_vocabulary_conflicts(vocab)
    assert any(c.token == "stt" for c in conflicts)


def test_conflict_detection_is_case_insensitive():
    vocab = parse_tag_vocabulary(
        {"tags": {"vocabulary": {"stt": ["ASR"], "speech": ["asr"]}}}
    )
    conflicts = detect_vocabulary_conflicts(vocab)
    assert any(c.token == "asr" for c in conflicts)


def test_off_vocabulary_has_no_conflicts():
    assert detect_vocabulary_conflicts(TagVocabulary(mode="off", vocabulary={})) == []
