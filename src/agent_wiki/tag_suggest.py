"""Draft tag-vocabulary suggestion engine (REQ-10).

Pure string heuristics — no ML. From a frequency map of in-use tags this groups
obvious related tags (shared hyphen/underscore token, or one a prefix of another)
into (preferred, alias-candidates) clusters, picks the most frequent tag of each
cluster as preferred, and renders a syntactically valid draft 'tags:' block with
per-tag frequency comments. The scan that produces the frequency map and the
merge into wiki.yaml live here too, but the clustering/rendering stay I/O-free
and unit-testable."""

import re
from collections import Counter

from agent_wiki.page import parse_page

# Tokens shorter than this are too generic to group on (e.g. 'ml', 'os').
_MIN_TOKEN = 3
_SPLIT = re.compile(r"[-_/\s]+")


def _tokens(tag: str) -> set:
    """Significant lower-cased tokens of a tag (split on - _ / whitespace)."""
    return {t for t in _SPLIT.split(tag.lower()) if len(t) >= _MIN_TOKEN}


def _related(a: str, b: str) -> bool:
    """True when two tags should share a cluster: a common significant token, or
    one is a prefix of the other (length >= _MIN_TOKEN)."""
    if _tokens(a) & _tokens(b):
        return True
    lo, hi = sorted((a.lower(), b.lower()), key=len)
    return len(lo) >= _MIN_TOKEN and hi.startswith(lo)


def cluster_tags(counts: dict) -> list:
    """Group tags from a {tag: frequency} map into (preferred, [aliases]) clusters.

    Tags are linked transitively by `_related`; each connected component is one
    cluster. Its preferred term is the most frequent tag (ties broken by shortest,
    then alphabetical); the remaining tags are alias candidates ordered by
    frequency (desc) then name. Clusters are returned ordered by the preferred
    term's frequency (desc) then name."""
    tags = list(counts)
    parent = {t: t for t in tags}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(tags):
        for b in tags[i + 1:]:
            if _related(a, b):
                parent[find(a)] = find(b)

    groups: dict = {}
    for t in tags:
        groups.setdefault(find(t), []).append(t)

    # Within a cluster: most frequent wins (then shortest, then alphabetical).
    rank = lambda t: (-counts[t], len(t), t)
    clusters = []
    for members in groups.values():
        members.sort(key=rank)
        preferred, *aliases = members
        clusters.append((preferred, aliases))

    clusters.sort(key=lambda c: (-counts[c[0]], c[0]))
    return clusters


def render_suggestion_block(clusters: list, counts: dict, mode: str = "warn") -> str:
    """Render clusters as a draft 'tags:' block (valid YAML, freq as comments)."""
    lines = ["tags:", f"  mode: {mode}"]
    if not clusters:
        lines.append("  vocabulary: {}")
        return "\n".join(lines) + "\n"
    lines.append("  vocabulary:")
    for preferred, aliases in clusters:
        lines.append(f"    {preferred}:  # {counts.get(preferred, 0)}")
        for alias in aliases:
            lines.append(f"    - {alias}  # {counts.get(alias, 0)}")
    return "\n".join(lines) + "\n"


def scan_tag_counts(vault_path, config: dict) -> dict:
    """Count frontmatter tag occurrences across every topic-folder page."""
    counts: Counter = Counter()
    for topic in config.get("topics", []):
        topic_dir = vault_path / topic
        if not topic_dir.is_dir():
            continue
        for md_file in topic_dir.rglob("*.md"):
            meta = parse_page(md_file)["meta"] or {}
            for tag in meta.get("tags") or []:
                counts[str(tag)] += 1
    return dict(counts)


def merge_clusters(existing: dict, clusters: list) -> dict:
    """Merge suggested clusters into an existing preferred→aliases mapping.

    Preserves every existing entry, reuses an existing key's casing, and unions
    alias candidates without duplicating (case-insensitively) or aliasing a term
    to itself."""
    merged = {k: list(v) for k, v in existing.items()}
    for preferred, aliases in clusters:
        key = next((k for k in merged if k.lower() == preferred.lower()), preferred)
        current = list(merged.get(key, []))
        seen = {a.lower() for a in current} | {key.lower()}
        for alias in aliases:
            if alias.lower() not in seen:
                current.append(alias)
                seen.add(alias.lower())
        merged[key] = current
    return merged
