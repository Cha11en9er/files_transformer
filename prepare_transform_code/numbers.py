import re

_CURRENCY = (
    ("CNY", ("CNY", "RMB", "¥")),
    ("USD", ("US$", "USD", "$")),
    ("EUR", ("EUR", "EURO", "€")),
)


def parse_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).replace("\u00a0", " ").replace("¥", " ").replace("€", " ")
    raw = raw.replace("$", " ")
    match = re.search(r"-?\d[\d\s]*([.,]\d+)?", raw)
    if not match:
        return None
    token = match.group(0).replace(" ", "")
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


def currency_of(text):
    blob = text.upper()
    if "¥" in text or "CNY" in blob or "RMB" in blob:
        return "CNY"
    if "€" in text or "EUR" in blob or "EURO" in blob:
        return "EUR"
    if "US$" in blob or "USD" in blob or "$" in text:
        return "USD"
    return None


def collapse_letter_spacing(text):
    """Схлопнуть ряд одиночных символов: «C I 2 4» → «CI24». Обычные слова не трогать."""
    parts = text.split()
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
