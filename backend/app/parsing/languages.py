"""Language / script / encoding helpers.

Add a new language by appending to LANGUAGES. Detection is script-based plus
header hints; unknown languages still parse as the same fields (article, qty, price).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.parsing.normalize import normalize_text


@dataclass(frozen=True)
class LanguageSpec:
    id: str
    name: str
    scripts: tuple[str, ...]
    encodings: tuple[str, ...]
    header_hints: tuple[str, ...]
    notes: str


# Append here when a new supplier language shows up. Do not hard-code a closed list
# in the model prompt: always also say "or another language".
LANGUAGES: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        "ru",
        "Russian",
        ("cyrillic",),
        ("utf-8", "cp1251", "koi8-r"),
        ("артикул", "наименование", "количество", "инвойс", "упаковочный", "спецификация", "нетто", "брутто"),
        "Cyrillic headers. Same fields as English.",
    ),
    LanguageSpec(
        "en",
        "English",
        ("latin",),
        ("utf-8", "cp1252", "latin-1"),
        ("invoice", "packing", "specification", "article", "quantity", "amount"),
        "Default commercial English.",
    ),
    LanguageSpec(
        "zh",
        "Chinese",
        ("cjk",),
        ("utf-8", "gb18030", "gbk", "big5"),
        ("货号", "品号", "品名", "数量", "单价", "金额", "发票", "装箱单", "规格", "净重", "毛重"),
        "Letterhead often 杭州 / 浙江. Goods lines are usually English or Russian.",
    ),
    LanguageSpec(
        "tr",
        "Turkish",
        ("latin",),
        ("utf-8", "cp1254", "iso-8859-9"),
        (
            "ürün kodu",
            "urun kodu",
            "müşteri kodu",
            "musteri kodu",
            "seçme listesi",
            "secme listesi",
            "çeki",
            "ceki",
            "net metre",
            "brüt kilogram",
            "genel toplam",
            "sipariş",
        ),
        "İ/ı/ş/ğ/ç/ö/ü. Packing often has no cell borders. Sheet name Ceki = packing.",
    ),
    LanguageSpec(
        "it",
        "Italian",
        ("latin",),
        ("utf-8", "cp1252", "latin-1"),
        ("foglio", "articolo", "hides", "packing list"),
        "Mora-style packing: ARTICLE + COLOUR + HIDES + m2. Sheet Foglio1.",
    ),
)

SCRIPT_PATTERNS: dict[str, re.Pattern[str]] = {
    "cyrillic": re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]"),
    "cjk": re.compile(r"[\u3400-\u9fff\uF900-\uFAFF\u3040-\u30ff]"),
    "arabic": re.compile(r"[\u0600-\u06ff]"),
    "hangul": re.compile(r"[\uac00-\ud7af]"),
    "latin": re.compile(r"[A-Za-z]"),
}

TURKISH_LETTERS = re.compile(r"[İıŞşĞğÇçÖöÜü]")

BYTE_ENCODINGS: tuple[str, ...] = (
    "utf-8-sig",
    "utf-8",
    "gb18030",
    "gbk",
    "big5",
    "cp1254",
    "iso-8859-9",
    "cp1251",
    "cp1252",
    "koi8-r",
    "latin-1",
)


def detect_scripts(text: str) -> list[str]:
    blob = text or ""
    found: list[str] = []
    for name, pattern in SCRIPT_PATTERNS.items():
        if pattern.search(blob):
            found.append(name)
    if TURKISH_LETTERS.search(blob) and "latin" not in found:
        found.append("latin")
    return found


def detect_languages(text: str) -> list[str]:
    """Return language ids that are plausible for this blob. Order is stable."""
    blob = normalize_text(text).lower()
    scripts = set(detect_scripts(text))
    hits: list[str] = []
    for spec in LANGUAGES:
        script_hit = any(script in scripts for script in spec.scripts if script != "latin")
        hint_hit = any(hint in blob for hint in spec.header_hints)
        turkish_hit = spec.id == "tr" and bool(TURKISH_LETTERS.search(text or ""))
        latin_en = spec.id == "en" and "latin" in scripts
        if script_hit or hint_hit or turkish_hit or latin_en:
            hits.append(spec.id)
    if "arabic" in scripts:
        hits.append("ar")
    if "hangul" in scripts:
        hits.append("ko")
    # de-dupe keep order
    seen: set[str] = set()
    ordered: list[str] = []
    for item in hits:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def language_names(ids: list[str]) -> list[str]:
    by_id = {spec.id: spec.name for spec in LANGUAGES}
    extra = {"ar": "Arabic", "ko": "Korean"}
    return [by_id.get(item) or extra.get(item) or item for item in ids]


def _native_script_score(text: str, encoding: str) -> float:
    """How well decoded text matches the script this encoding is for."""
    sample = text[:8000]
    n = max(len(sample), 1)
    cjk = sum(1 for ch in sample if 0x4E00 <= ord(ch) <= 0x9FFF)
    cyr = len(SCRIPT_PATTERNS["cyrillic"].findall(sample))
    latin = sum(1 for ch in sample if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
    pua = sum(1 for ch in sample if 0xE000 <= ord(ch) <= 0xF8FF)
    if pua:
        return -1.0
    if encoding in {"gb18030", "gbk", "big5"}:
        return cjk / n
    if encoding in {"cp1251", "koi8-r"}:
        return cyr / n
    if encoding in {"cp1254", "iso-8859-9"}:
        tr = len(TURKISH_LETTERS.findall(sample))
        if tr == 0:
            return 0.08 * (latin / n)
        return min(1.0, (tr * 2 + latin) / n)
    if encoding == "cp1252":
        return 0.12 * (latin / n)
    if encoding == "latin-1":
        return 0.05
    return 0.0


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode unknown text bytes. Returns (text, encoding_used)."""
    if not data:
        return "", "utf-8"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    best: tuple[float, int, str, str] | None = None
    for index, encoding in enumerate(BYTE_ENCODINGS):
        if encoding.startswith("utf-8"):
            continue
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        score = _native_script_score(text, encoding)
        candidate = (score, -index, encoding, text)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return data.decode("latin-1", errors="replace"), "latin-1"
    return best[3], best[2]


def repair_mojibake(text: str) -> str:
    """Fix UTF-8 that was shown as Latin-1 (Ð/Ñ/Ã). Leave CJK/Cyrillic alone."""
    if not text:
        return text
    if not any(marker in text for marker in ("Ð", "Ñ", "Ã", "Â")):
        return text
    if SCRIPT_PATTERNS["cyrillic"].search(text) or SCRIPT_PATTERNS["cjk"].search(text):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text
    if repaired.count("\ufffd") >= text.count("\ufffd"):
        if detect_scripts(repaired) and not detect_scripts(text):
            return repaired
        return text
    return repaired


def language_prompt_block(text: str) -> str:
    scripts = detect_scripts(text)
    langs = detect_languages(text)
    names = language_names(langs)
    extra = [spec.id for spec in LANGUAGES if spec.id not in langs]
    extra_names = language_names(extra)
    prepared = f" Also prepared for: {', '.join(extra_names)}." if extra_names else ""
    return (
        "languages_in_this_shipment: "
        + (", ".join(names) if names else "not detected yet")
        + f". scripts: {', '.join(scripts) or '-'}. "
        "A new language may appear without a template. Keep reading tables: "
        "map unknown headers to article / qty / meters / price / amount / net / gross by "
        "neighbouring numbers and bilingual titles (EN/RU, 货号/article, Ürün Kodu/article). "
        "Do not drop a table because the language is new. "
        "Bytes may be utf-8, utf-8-sig, gb18030/gbk/big5, cp1251, cp1254, iso-8859-9, utf-16."
        + prepared
    )
