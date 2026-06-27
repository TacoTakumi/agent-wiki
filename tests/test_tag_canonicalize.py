from agent_wiki.config import TagVocabulary, parse_tag_vocabulary
from agent_wiki.tags import canonicalize_tags


def _vocab(mapping, mode="warn"):
    return parse_tag_vocabulary({"tags": {"mode": mode, "vocabulary": mapping}})


STT = _vocab({"stt": ["asr"], "gpu": []})


def test_alias_is_rewritten_to_preferred_with_remap():
    result = canonicalize_tags(["asr"], STT)
    assert result.tags == ["stt"]
    assert result.remaps == [("asr", "stt")]
    assert result.novel == []


def test_casing_of_preferred_is_normalized_without_a_remap():
    result = canonicalize_tags(["STT"], STT)
    assert result.tags == ["stt"]
    assert result.remaps == []
    assert result.novel == []


def test_alias_match_is_case_insensitive_and_preserves_input_in_remap():
    result = canonicalize_tags(["ASR"], STT)
    assert result.tags == ["stt"]
    assert result.remaps == [("ASR", "stt")]


def test_collapsing_inputs_dedupe_order_preserving():
    result = canonicalize_tags(["asr", "stt"], STT)
    assert result.tags == ["stt"]
    assert result.remaps == [("asr", "stt")]


def test_novel_tag_is_kept_and_flagged():
    result = canonicalize_tags(["frobnicate"], STT)
    assert result.tags == ["frobnicate"]
    assert result.novel == ["frobnicate"]
    assert result.remaps == []


def test_order_is_preserved_across_mixed_tags():
    result = canonicalize_tags(["gpu", "asr", "frob"], STT)
    assert result.tags == ["gpu", "stt", "frob"]
    assert result.remaps == [("asr", "stt")]
    assert result.novel == ["frob"]


def test_preferred_casing_from_vocabulary_is_honored():
    vocab = _vocab({"STT": ["asr"]})
    assert canonicalize_tags(["asr"], vocab).tags == ["STT"]
    assert canonicalize_tags(["asr"], vocab).remaps == [("asr", "STT")]
    assert canonicalize_tags(["stt"], vocab).tags == ["STT"]


def test_off_vocabulary_passes_tags_through_untouched():
    off = TagVocabulary(mode="off", vocabulary={})
    result = canonicalize_tags(["asr", "asr", "STT"], off)
    assert result.tags == ["asr", "asr", "STT"]
    assert result.remaps == []
    assert result.novel == []


def test_empty_input_yields_empty_result():
    result = canonicalize_tags([], STT)
    assert result.tags == []
    assert result.remaps == []
    assert result.novel == []


def test_no_io_pure_function_does_not_touch_vocab_object():
    before = dict(STT.vocabulary)
    canonicalize_tags(["asr", "frob"], STT)
    assert STT.vocabulary == before


def test_non_string_novel_tag_rides_through_without_crashing():
    # A malformed non-string tag (e.g. a YAML bare int `tags: [2024]`) must not
    # crash canonicalization: it's kept verbatim and flagged novel, so read-only
    # lint/tag-audit and tag fix stay traceback-free on bad frontmatter.
    result = canonicalize_tags([2024], STT)
    assert result.tags == [2024]
    assert result.novel == [2024]
    assert result.remaps == []


def test_non_string_novel_tag_preserves_type_alongside_canonicalized_strings():
    result = canonicalize_tags(["asr", 2024], STT)
    # 'asr' canonicalizes to 'stt'; the int rides through unchanged (still an int).
    assert result.tags == ["stt", 2024]
    assert isinstance(result.tags[1], int)   # type preserved, not stringified
    assert result.remaps == [("asr", "stt")]
    assert result.novel == [2024]
