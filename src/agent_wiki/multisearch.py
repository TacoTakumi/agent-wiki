"""Search several vaults and merge the hits into one coverage-ranked list.

`awiki search` and the viewer search both rank cross-vault results through
`merged_search`, so the two always agree. It takes plain registry entries
(local or remote) and never prints: vaults that cannot be searched come back
as (name, reason) pairs for the caller to report its own way.
"""


def merged_search(vaults, query, topic=None, limit=20, partial_limit=5):
    """Search every vault in `vaults` (name -> VaultEntry) and merge into one
    coverage-ranked result dict (same shape a VaultService search returns),
    with every hit path vault-qualified and its vault name under "vault". A
    vault-qualified `topic` narrows the sweep to that vault.

    Returns (result, skipped), where skipped lists (vault name, reason) for
    each vault whose search raised, in search order."""
    from agent_wiki.config import backend_for_entry
    from agent_wiki.resolve import split_vault_ref

    entries = [vaults[name] for name in sorted(vaults)]
    if topic:
        topic_entry, topic_ref = split_vault_ref(topic, vaults)
        if topic_entry is not None:
            entries = [topic_entry]
            topic = topic_ref

    all_pool, partial_pool, total, skipped = [], [], 0, []
    for entry in entries:
        try:
            out = backend_for_entry(entry).search(
                query, topic=topic, limit=limit)
        except Exception as e:
            skipped.append((entry.name, str(e)))
            continue
        for r in out["all"] + out["partial"]:
            r["vault"] = entry.name
            r["path"] = f"{entry.name}:{r['path']}"
        all_pool.extend(out["all"])
        partial_pool.extend(out["partial"])
        total += out["total"]

    # The same ordering search._rank uses, applied across vaults.
    def rank_key(r):
        return (-r["coverage"], -len(r["matches"]), r["title"])

    all_pool.sort(key=rank_key)
    partial_pool.sort(key=rank_key)
    shown_all = all_pool[:limit]
    shown_partial = partial_pool[:partial_limit]
    shown = len(shown_all) + len(shown_partial)
    return {
        "all": shown_all,
        "partial": shown_partial,
        "total": total,
        "shown": shown,
        "truncated": shown < total,
    }, skipped
