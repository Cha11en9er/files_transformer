import contextvars
import re

_DOT_IS_DECIMAL = contextvars.ContextVar("dot_is_decimal", default=False)
_COMMA_IS_DECIMAL = contextvars.ContextVar("comma_is_decimal", default=False)

_CURRENCY = (
    ("CNY", ("CNY", "RMB", "¥")),
    ("USD", ("US$", "USD", "$")),
    ("EUR", ("EUR", "EURO", "€")),
)


# Точка группами по три — тысячи (3.900, 5.952,40). Точка с 1–2 знаками (21.03, 6809.6) — дробь.
_NUMBER = re.compile(
    r"(?<![\d])-?\d{1,3}(?:\.\d{3})+(?:,\s*\d+)?(?![\d])"
    r"|(?<![\d])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d])"
    r"|(?<![\d])-?\d[\d ]*[.,]\d+"
    r"|(?<![\d])-?\d[\d ]*"
)


def parse_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).replace("\u00a0", " ").replace("\n", "")
    raw = raw.replace("¥", " ").replace("€", " ").replace("$", " ")
    match = _NUMBER.search(raw)
    if not match:
        return None
    return _interpret(match.group(0).replace(" ", ""))


def dot_is_decimal(text):
    """В тексте есть 1,688.90 или 2,638.800: одинокое 82.300 - это 82.3, не 82300."""
    return bool(re.search(r"\d{1,3}(?:,\d{3})+\.\d+", text or ""))


def comma_is_decimal(text):
    """На листе 516,30 и 6,35: запятая — дробь, и 722,820 — это 722.820, не 722820."""
    blob = text or ""
    if re.search(r"\d{1,3}(?:,\d{3})+\.\d+", blob):
        return False
    return bool(re.search(r"\d+,\d{1,2}(?!\d)", blob))


def _interpret(token):
    if _COMMA_IS_DECIMAL.get() and re.fullmatch(r"-?\d+,\d+", token):
        try:
            return float(token.replace(",", "."))
        except ValueError:
            return None
    if _DOT_IS_DECIMAL.get() and re.fullmatch(r"-?\d{1,3}\.\d{3}", token):
        try:
            return float(token)
        except ValueError:
            return None
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", token):
        sign = -1 if token.startswith("-") else 1
        body = token[1:] if token.startswith("-") else token
        if "," in body:
            whole, frac = body.split(",", 1)
            token = whole.replace(".", "") + "." + frac
        else:
            token = body.replace(".", "")
        try:
            return sign * float(token)
        except ValueError:
            return None
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", token):
        sign = -1 if token.startswith("-") else 1
        body = token[1:] if token.startswith("-") else token
        if "." in body:
            whole, frac = body.split(".", 1)
            token = whole.replace(",", "") + "." + frac
        else:
            token = body.replace(",", "")
        try:
            return sign * float(token)
        except ValueError:
            return None
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:
            token = parts[0] + "." + parts[1]
        elif all(len(part) == 3 for part in parts[1:]):
            token = "".join(parts)
        else:
            token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


_CURRENCY_MARKS = (
    ("CNY", ("CNY", "RMB", "YUAN", "ЮАН")),
    ("EUR", ("EUR", "EURO", "ЕВРО")),
    ("USD", ("USD", "US$", "ДОЛЛ")),
    ("GBP", ("GBP",)),
    ("PLN", ("PLN", "ZŁ", "ZŁOT")),
    ("SEK", ("SEK",)),
    ("RUB", ("RUB", "РУБ")),
    ("TRY", ("TRY",)),
    ("CHF", ("CHF",)),
    ("JPY", ("JPY", "YEN", "ИЕН")),
    ("KRW", ("KRW", "WON")),
    ("INR", ("INR",)),
    ("KZT", ("KZT", "ТЕНГЕ")),
    ("UAH", ("UAH", "ГРИВ")),
    ("CZK", ("CZK",)),
    ("HUF", ("HUF",)),
)


def currencies_of(text):
    """Все валюты документа, без выбора одной. Символ и код, не название страны."""
    blob = str(text or "").upper()
    found = []
    if "¥" in str(text or "") and "CNY" not in found:
        found.append("CNY")
    if "€" in str(text or "") and "EUR" not in found:
        found.append("EUR")
    if "£" in str(text or "") and "GBP" not in found:
        found.append("GBP")
    if "₺" in str(text or "") and "TRY" not in found:
        found.append("TRY")
    original = str(text or "")
    for code, marks in _CURRENCY_MARKS:
        if code in found:
            continue
        # «Rub Off» в цвете — не рубль. Код рубля пишут RUB или «руб».
        if code == "RUB":
            if re.search(r"(?<![A-Z])RUB(?![A-Z])", original) or re.search(
                r"(?<![А-ЯЁ])РУБ(?![А-ЯЁ])", blob
            ):
                found.append("RUB")
            continue
        for mark in marks:
            if mark == "EURO":
                # «EURO MAX» — имя судна, не валюта. Евро — слово само по себе или рядом с числом.
                for match in re.finditer(r"(?<![A-Z])EURO(?![A-Z])", blob):
                    if re.match(r"\s+[A-Z]{2,}", blob[match.end() :]):
                        continue
                    found.append("EUR")
                    break
                continue
            if re.search(rf"(?<![A-ZА-ЯЁ]){re.escape(mark)}(?![A-ZА-ЯЁ])", blob):
                found.append(code)
                break
    if "$" in str(text or "") and "USD" not in found:
        found.append("USD")
    return found


def goods_currency(text):
    """Валюта у колонки цены или суммы. Валюта счёта в подвале — не она."""
    found = []
    for match in re.finditer(
        r"(?:amount|price|сумма|цена)\s*[,:(/]?\s*(?:in\s+)?(RUB|USD|EUR|CNY|GBP|TRY|PLN|CHF|JPY|руб)",
        str(text or ""),
        re.I,
    ):
        code = match.group(1).upper()
        if code == "РУБ":
            code = "RUB"
        if code not in found:
            found.append(code)
    if found:
        return found[0]
    # Шапка порвана: AMOUNT, на следующей строке USD. Юань в условии оплаты сюда не попадает.
    for match in re.finditer(r"(?:amount|price|сумма|цена)\s*,", str(text or ""), re.I):
        window = str(text or "")[match.end() : match.end() + 220]
        code = re.search(r"\b(RUB|USD|EUR|CNY|GBP|TRY|PLN|CHF|JPY)\b", window, re.I)
        if not code:
            continue
        before = window[: code.start()]
        if re.search(r"\d{6,}", before):
            continue
        return code.group(1).upper()
    return None


def currency_of(text):
    found = currencies_of(text)
    for code in ("CNY", "EUR", "USD"):
        if code in found:
            return code
    return found[0] if found else None


def row_is_doubled(row):
    """Текстовый слой, где каждый знак напечатан дважды. Короткая «22» сама по себе не такая."""
    compact = "".join(ch for ch in "".join(str(cell or "") for cell in row) if not ch.isspace())
    if len(compact) < 12:
        return False
    doubled = 0
    index = 0
    while index + 1 < len(compact):
        if compact[index] == compact[index + 1]:
            doubled += 2
            index += 2
        else:
            index += 1
    return doubled >= len(compact) * 0.75


def undouble_text(text):
    if not isinstance(text, str) or len(text) < 2:
        return text
    out = []
    index = 0
    while index < len(text):
        if index + 1 < len(text) and text[index] == text[index + 1] and not text[index].isspace():
            out.append(text[index])
            index += 2
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def undouble_row(row):
    if not row_is_doubled(row):
        return row
    return [undouble_text(cell) if isinstance(cell, str) else cell for cell in row]


def collapse_letter_spacing(text):
    """Схлопнуть ряд одиночных символов: «C I 2 4» → «CI24». Обычные слова не трогать."""
    parts = text.split()
    if len(parts) >= 2 and all(len(part) == 1 for part in parts):
        return "".join(parts)
    out = []
    buf = []

    def flush():
        if len(buf) >= 4:
            out.append("".join(buf))
        else:
            out.extend(buf)
        buf.clear()

    for part in parts:
        if len(part) == 1:
            buf.append(part)
        else:
            flush()
            out.append(part)
    flush()
    return " ".join(out)


_PACKAGE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:plywood\s*)?(?:cartons?|cases?|packages?|rolls?|pkgs?)",
    re.I,
)


def package_count(text):
    if not text:
        return None
    found = [parse_number(m.group(1)) for m in _PACKAGE_RE.finditer(str(text))]
    found = [n for n in found if n is not None]
    if not found:
        return None
    return float(sum(found))


def is_package_text(text):
    return bool(re.search(r"carton|case|package|plywood|roll|pkg", str(text), re.I))


def is_piece_text(text):
    return bool(re.search(r"\b(sets?|pcs|pc|psc|pairs?|шт)\b", str(text), re.I))
