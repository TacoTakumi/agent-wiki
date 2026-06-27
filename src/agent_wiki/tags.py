"""Pure tag-canonicalization engine over a TagVocabulary.

No I/O: callers load the vocabulary (config.load_tag_vocabulary) and pass a tag
list in. The single write boundary (ingest_file) runs page tags through here
before writing."""

from dataclasses import dataclass, field

from agent_wiki.config import TagVocabulary


@dataclass(frozen=True)
class CanonicalizeResult:
    """The outcome of canonicalizing a tag list.

    `tags` is the canonical, order-preserving, de-duplicated list to write.
    `remaps` is the list of (input, preferred) pairs where an alias was rewritten
    to its preferred term — casing-only normalization of a preferred term is not a
    remap. `novel` is the inputs that matched no preferred term or alias."""

    tags: list = field(default_factory=list)
    remaps: list = field(default_factory=list)
    novel: list = field(default_factory=list)


def canonicalize_tags(tags, vocab: TagVocabulary) -> CanonicalizeResult:
    """Canonicalize `tags` against `vocab`.

    Each input is matched case-insensitively: a preferred term passes through in
    the vocabulary's canonical casing; an alias is rewritten to its preferred term
    (recorded as a remap); anything else is kept verbatim and flagged novel. The
    result is de-duplicated order-preservingly (collapsing inputs yield one tag).

    With an off/empty vocabulary the input is returned untouched — no casing
    change, no de-dup, no novel flags — so non-vocabulary vaults are unaffected."""
    tags = list(tags)
    if vocab.is_off:
        return CanonicalizeResult(tags=tags, remaps=[], novel=[])

    preferred_by_lower = {p.lower(): p for p in vocab.vocabulary}
    alias_to_preferred = {}
    for preferred, aliases in vocab.vocabulary.items():
        for alias in aliases:
            alias_to_preferred.setdefault(alias.lower(), preferred)

    out: list = []
    remaps: list = []
    novel: list = []
    seen: set = set()

    for tag in tags:
        lower = str(tag).lower()
        if lower in preferred_by_lower:
            canonical = preferred_by_lower[lower]
        elif lower in alias_to_preferred:
            canonical = alias_to_preferred[lower]
            remaps.append((tag, canonical))
        else:
            canonical = tag
            novel.append(tag)
        if canonical.lower() not in seen:
            seen.add(canonical.lower())
            out.append(canonical)

    return CanonicalizeResult(tags=out, remaps=remaps, novel=novel)
