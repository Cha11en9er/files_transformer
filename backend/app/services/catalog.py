"""Reference catalog loader — Word spec §4.2 / MD 812 example.

Only fill TN VED + bilingual description when article matches.
Never invent codes for missing articles (flag instead).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.parsing.excel_reader import read_excel
from app.parsing.normalize import normalize_article
from app.services.field_map import map_row


class CatalogIndex:
    def __init__(self) -> None:
        self._by_article: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_excel(cls, path: str | Path) -> CatalogIndex:
        index = cls()
        _sheets, lines, _preview = read_excel(str(path))
        for line in lines:
            key = line.get("normalized_article") or ""
            if not key:
                mapped = map_row(line.get("raw") or {})
                key = normalize_article(mapped.get("article") or mapped.get("model"))
            if not key:
                continue
            mapped = map_row(line.get("raw") or {})
            # Prefer first hit; do not silently overwrite with later ambiguous rows
            if key not in index._by_article:
                index._by_article[key] = {
                    "article": line.get("article") or mapped.get("article"),
                    "tnved_code": mapped.get("tnved_code") or mapped.get("hs_code"),
                    "description_en": mapped.get("description_en") or mapped.get("description"),
                    "description_ru": mapped.get("description_ru"),
                    "raw": line.get("raw") or {},
                }
        return index

    def lookup(self, normalized_article: str) -> dict[str, Any] | None:
        if not normalized_article:
            return None
        return self._by_article.get(normalized_article)

    @property
    def size(self) -> int:
        return len(self._by_article)
