import pdfplumber

from prepare_transform_code.fields import column_of, is_stop_label
from prepare_transform_code.lines import Line, fill_qty
from prepare_transform_code.numbers import collapse_letter_spacing, currency_of

_ROLE_MARKS = (
    ("packing", ("PACKING LIST", "УПАКОВОЧН")),
    ("invoice", ("COMMERCIAL INVOICE", "PROFORMA", "INVOICE", "СЧЕТ-ФАКТУРА")),
    ("specification", ("СПЕЦИФИКАЦИЯ", "SPECIFICATION")),
)


def read_pdf(path):
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            pages.append(
                {
                    "rotation": page.rotation or 0,
                    "chars": len(page.chars or []),
                    "images": len(page.images or []),
                    "text": text,
                    "tables": tables,
                }
            )
    raw = "\n".join(page["text"] for page in pages)
    text = collapse_letter_spacing(raw)
    readable = _readable(text)
    return {
        "kind": "pdf",
        "text": text,
        "raw_text": raw,
        "pages": len(pages),
        "rotation": pages[0]["rotation"] if pages else 0,
        "images": sum(page["images"] for page in pages),
        "chars": sum(page["chars"] for page in pages),
        "readable": readable,
        "role": _role(text) if readable else "scan",
        "currency": currency_of(text),
        "lines": _lines(pages) if readable else [],
        "tables": [table for page in pages for table in page["tables"]],
    }


def _readable(text):
    upper = text.upper()
    marks = ("INVOICE", "PACKING", "DESCRIPTION", "QTY", "СПЕЦИФИКАЦИЯ", "ИНВОЙС")
    return any(mark in upper for mark in marks)


def _role(text):
    upper = text.upper()
    for role, marks in _ROLE_MARKS:
        if any(mark in upper for mark in marks):
            return role
    return "unknown"


def _lines(pages):
    lines = []
    for page in pages:
        for table in page["tables"]:
            lines.extend(_table_lines(table))
    return _share_measures(lines)


def _map_row(row):
    mapped = {}
    for col, cell in enumerate(row):
        name = column_of(cell)
        if name and name not in mapped:
            mapped[name] = col
    return mapped


def _table_lines(table):
    header_idx = None
    mapping = {}
    for index, row in enumerate(table):
        mapped = _map_row(row)
        if {"description", "qty"} & set(mapped) and len(mapped) >= 2:
            header_idx = index
            mapping = mapped
            break
    if header_idx is None:
        return []
    cursor = header_idx + 1
    while cursor < len(table) and _header_continuation(table[cursor]):
        for name, col in _map_row(table[cursor]).items():
            mapping.setdefault(name, col)
        cursor += 1
    lines = []
    for row in table[cursor:]:
        if any(is_stop_label(cell) for cell in row if cell):
            break
        line = Line(source="pdf")
        for name, col in mapping.items():
            if col >= len(row):
                continue
            value = row[col]
            if value is None:
                continue
            text = str(value).replace("\n", " ").strip()
            if not text:
                continue
            if name == "description":
                line.description = text
            elif name == "model":
                line.model = text
            elif name == "vendor":
                line.vendor = text
            elif name == "qty":
                fill_qty(line, text, header_is_package=(mapping.get("packages") == col))
            elif name == "packages":
                fill_qty(line, text, header_is_package=True)
            elif name == "price":
                from prepare_transform_code.numbers import parse_number

                line.price = parse_number(text)
            elif name == "amount":
                from prepare_transform_code.numbers import parse_number

                line.amount = parse_number(text)
            elif name == "gross":
                from prepare_transform_code.numbers import parse_number

                line.gross = parse_number(text)
            elif name == "net":
                from prepare_transform_code.numbers import parse_number

                line.net = parse_number(text)
            elif name == "volume":
                from prepare_transform_code.numbers import parse_number

                line.volume = parse_number(text)
        identified = bool(line.model) or _real_code(line.vendor)
        measured = any(getattr(line, name) is not None for name in ("pieces", "packages", "price", "amount", "gross", "net"))
        if identified or (line.description and measured):
            lines.append(line)
    return lines


def _real_code(vendor):
    text = str(vendor or "").strip().strip("-")
    return bool(text) and text.lower() not in {"na", "n/a"}


def _header_continuation(row):
    mapped = _map_row(row)
    if not mapped:
        return False
    if any(cell and len(str(cell)) > 40 for cell in row):
        return False
    return not any(is_stop_label(cell) for cell in row if cell)


def _share_measures(lines):
    """Вес и места, напечатанные одной клеткой на две модели, не отдавать только первой строке."""
    for index, line in enumerate(lines[:-1]):
        nxt = lines[index + 1]
        if line.gross is None or nxt.gross is not None:
            continue
        if not (nxt.model or nxt.description):
            continue
        qty_blob = f"{line.qty_text} {nxt.qty_text}"
        split_package = "&" in line.qty_text or (
            nxt.packages is None and nxt.pieces is None and nxt.qty_text and not any(ch.isdigit() for ch in nxt.qty_text)
        )
        if not split_package:
            continue
        group = {
            "gross": line.gross,
            "net": line.net,
            "packages": line.packages,
            "text": qty_blob.strip(),
        }
        line.measure_group = group
        nxt.measure_group = group
        line.gross = line.net = line.packages = None
        nxt.gross = nxt.net = nxt.packages = None
    return lines
