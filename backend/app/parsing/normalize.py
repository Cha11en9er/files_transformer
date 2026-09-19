from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+", flags=re.UNICODE)
_CONTROL_RE = re.compile(r"[\u0000-\u001f\u007f]")


def normalize_text(value: str | None) -> str:
    """Collapse whitespace and strip control characters. Empty if None."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = _CONTROL_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_article(value: str | None) -> str:
    """Technical article key used for strict cross-file matching.

    Rules (example from spec is authoritative):
    - Unicode NFKC, uppercase
    - remove all whitespace
    - keep hyphens: ``MO SHO-01`` -> ``MOSHO-01``
    - do not apply semantic / fuzzy grouping
    """
    text = normalize_text(value)
    if not text:
        return ""
    text = text.upper()
    text = re.sub(r"[\s\u00a0\u2000-\u200b\ufeff]+", "", text)
    return text


def tokens_for_search(value: str | None) -> str:
    """Lowercased blob used by document-type heuristics."""
    return normalize_text(value).lower()
