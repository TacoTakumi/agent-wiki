"""Mark search query terms in plain text with <mark>, as safe HTML.

The terms are the query split on whitespace. They are matched on the raw text
(before escaping), without regard to case, in one regex pass with the longest
term first, so a term never matches inside an HTML entity and a longer term
wins over a shorter one that starts at the same place. Every segment, inside
and between marks, is HTML-escaped. The result is a markupsafe.Markup, so a
Jinja template renders it as is.
"""

import re

from markupsafe import Markup, escape


def _terms_pattern(query: str) -> "re.Pattern[str] | None":
    """One case-insensitive alternation of the unique query terms, longest
    first, or None when the query has no terms."""
    terms = {term.lower() for term in query.split()}
    if not terms:
        return None
    ordered = sorted(terms, key=lambda term: (-len(term), term))
    return re.compile("|".join(re.escape(term) for term in ordered), re.IGNORECASE)


def highlight(text: str, query: str) -> Markup:
    """Return text as escaped HTML with each query term wrapped in <mark>."""
    pattern = _terms_pattern(query)
    if pattern is None:
        return escape(text)
    parts = []
    pos = 0
    for match in pattern.finditer(text):
        parts.append(escape(text[pos:match.start()]))
        parts.append(Markup("<mark>%s</mark>") % match.group(0))
        pos = match.end()
    parts.append(escape(text[pos:]))
    return Markup("").join(parts)
