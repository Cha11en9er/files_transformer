"""Extract tables via Firecrawl anydoc markdown, then generic table mapping."""

from __future__ import annotations

import os
import re
from typing import Any

from app.parsing.normalize import normalize_article, normalize_text
from app.parsing.product_row import is_product_article
from app.parsing.table_rows import (
    lines_from_matrix,
    lines_from_plaintext,
    lines_from_qty_price_text,
    merge_line_groups,
)

_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
_ARTIKEL_TOP_RE = re.compile(
    r"Artikel\s+(\d+)\s+Top\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)",
    re.IGNORECASE,
)
_WEAVERS_DESIGN_RE = re.compile(
    r"\b(?P<code>[A-Z]\d{2}-\d{4})\s*/\s*(?P<name>[A-Z][A-Z0-9]+)(?:\s+(?P<num>\d{2,4}))?",
)


def _split_md_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [normalize_text(cell) for cell in text.split("|")]


def _parse_tables(markdown: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            if _SEP_RE.match(stripped.replace("|", " ").replace(":", "-")):
                continue
            cells = _split_md_row(stripped)
            if any(cells):
                current.append(cells)
            continue
        if current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _num(raw: str) -> float:
    text_n = raw.strip()
    if text_n.count(",") == 1 and text_n.count(".") == 0:
        return float(text_n.replace(",", "."))
    if text_n.count(".") > 1 and "," in text_n:
        return float(text_n.replace(".", "").replace(",", "."))
    return float(text_n.replace(",", "."))


def _lines_from_packing_text(text: str) -> list[dict[str, Any]]:
    """Optional Tosun-style packing dump: Müşteri Kodu + Artikel N Top."""
    lines: list[dict[str, Any]] = []
    for match in re.finditer(
        r"M[uü][sş]teri\s+Kodu\s*:\s*([A-Z][A-Z0-9]+(?:\s+\d+)?)(.*?)(?=M[uü][sş]teri\s+Kodu\s*:|Genel Toplam|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        article = normalize_text(match.group(1))
        block = match.group(2)
        if not is_product_article(article):
            continue
        tops = list(_ARTIKEL_TOP_RE.finditer(block))
        if not tops:
            continue
        last = tops[-1]
        raw = {
            "articul/артикул": article,
            "number of rolls/количество рулонов/штук": float(last.group(1)),
            "q-ty meters / кол-во погонных метров": _num(last.group(2)),
            "g.w, kg / вес брутто, кг": _num(last.group(4)),
            "n.w, kg / вес нетто, кг": _num(last.group(5)),
        }
        lines.append(
            {
                "row_index": len(lines) + 1,
                "sheet_name": "packing_text",
                "article": article,
                "model": None,
                "normalized_article": normalize_article(article),
                "raw": raw,
            }
        )
    return lines


def _flatten_markdown(markdown: str) -> str:
    text = markdown.replace("|", " ")
    text = re.sub(r":?-{3,}:?", " ", text)
    text = re.sub(r"[#*_`]+", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def _lines_from_weavers_designs(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _WEAVERS_DESIGN_RE.finditer(text or ""):
        name = normalize_text(match.group("name"))
        num = match.group("num")
        article = f"{name} {num}" if num else name
        key = normalize_article(article)
        if not key or key in seen:
            continue
        seen.add(key)
        lines.append(
            {
                "row_index": len(lines) + 1,
                "sheet_name": "design_codes",
                "article": article,
                "model": match.group("code"),
                "normalized_article": key,
                "raw": {"article": article, "model": match.group("code")},
            }
        )
    return lines


def _lines_from_markdown(markdown: str) -> list[dict[str, Any]]:
    tables = _parse_tables(markdown)
    table_lines: list[dict[str, Any]] = []
    for idx, table in enumerate(tables):
        table_lines.extend(lines_from_matrix(table, sheet_name=f"anydoc_{idx + 1}"))
    flat = _flatten_markdown(markdown)
    lowered = markdown.lower()
    extra: list[dict[str, Any]] = []
    if "musteri kodu" in lowered or "müşteri kodu" in lowered:
        extra.extend(_lines_from_packing_text(markdown))
    extra.extend(lines_from_qty_price_text(flat, sheet_name="anydoc_qty_price"))
    extra.extend(lines_from_plaintext(flat, sheet_name="anydoc_text"))
    extra.extend(_lines_from_weavers_designs(markdown))
    return merge_line_groups(table_lines, extra)


def read_anydoc(path: str) -> dict[str, Any] | None:
    try:
        import anydoc
    except ImportError:
        return None

    try:
        markdown = anydoc.to_markdown(str(path))
    except getattr(anydoc, "NeedsOcrError", Exception) as exc:
        if exc.__class__.__name__ != "NeedsOcrError":
            return {"text": "", "lines": [], "warnings": [f"anydoc_failed:{exc}"], "needs_ocr": False}
        hosted = os.getenv("FIRECRAWL_API_KEY")
        if hosted:
            try:
                markdown = anydoc.to_markdown(str(path), ocr="hosted", api_key=hosted)
            except Exception as hosted_exc:
                return {
                    "text": "",
                    "lines": [],
                    "warnings": [f"anydoc_needs_ocr:{exc}", f"anydoc_hosted_failed:{hosted_exc}"],
                    "needs_ocr": True,
                }
        else:
            pages = getattr(exc, "pages", None)
            return {
                "text": "",
                "lines": [],
                "warnings": [f"anydoc_needs_ocr:{exc}"],
                "needs_ocr": True,
                "ocr_pages": list(pages) if pages else None,
            }
    except Exception as exc:
        return {"text": "", "lines": [], "warnings": [f"anydoc_failed:{exc}"], "needs_ocr": False}

    lines = _lines_from_markdown(markdown)
    return {
        "text": markdown,
        "lines": lines,
        "warnings": [] if lines else ["anydoc_no_product_rows"],
        "needs_ocr": False,
    }
