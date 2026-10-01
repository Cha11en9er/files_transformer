from openpyxl import load_workbook

from prepare_transform_code.fields import column_of, is_freight, is_stop_label
from prepare_transform_code.lines import Line, fill_qty
from prepare_transform_code.numbers import currency_of, parse_number

try:
    import xlrd
except ImportError:  # pragma: no cover
    xlrd = None


def read_excel(path):
    suffix = path.suffix.lower()
    if suffix == ".xls":
        sheets = _xls(path)
    else:
        sheets = _xlsx(path)
    docs = []
    for sheet in sheets:
        preview = _preview(sheet)
        role = _role(sheet["name"], sheet["rows"])
        lines = [] if role in {"draft", "gtd_form", "customs_appendix"} else _lines(sheet["rows"])
        docs.append(
            {
                "kind": "excel",
                "sheet": sheet["name"],
                "role": role,
                "text": preview,
                "currency": currency_of(preview),
                "readable": True,
                "lines": lines,
            }
        )
    return docs


def _preview(sheet):
    return sheet["name"] + "\n" + _join(sheet["rows"][:40])


def _role(name, rows):
    head = (name + "\n" + _join(rows[:4])).upper()
    whole = _join(rows).upper()
    if "ПРИЛОЖЕНИЕ К ИНВОЙСУ" in head or name.upper() == "ГТД":
        return "customs_appendix"
    if "ЧЕРНОВИК" in whole:
        return "draft"
    if "ФОРМА ДЛЯ ГТД" in head:
        return "gtd_form"
    if "PACKING LIST" in head or "УПАКОВОЧНЫЙ" in head:
        return "packing"
    if "СПЕЦИФИКАЦ" in head or "SPECIFICATION" in head:
        return "specification"
    if "INVOICE" in head or "СЧЕТ-ФАКТУРА" in head or "ИНВОЙС" in head:
        return "invoice"
    return "unknown"


def _join(rows):
    chunks = []
    for row in rows:
        chunks.extend(str(cell) for cell in row if cell not in (None, ""))
    return "\n".join(chunks)


def _lines(rows):
    header_idx, mapping = _header(rows)
    if header_idx is None:
        return []
    lines = []
    for row in rows[header_idx + 1 :]:
        texts = [str(cell).strip() for cell in row if cell not in (None, "")]
        if not texts:
            continue
        if any(is_stop_label(cell) for cell in texts):
            break
        if len(texts) == 1 and not any(ch.isdigit() for ch in texts[0]):
            continue
        line = Line(source="excel")
        for name, col in mapping.items():
            if col >= len(row):
                continue
            value = row[col]
            if value in (None, ""):
                continue
            if name == "description":
                line.description = str(value).replace("\n", " ").strip()
            elif name == "model":
                line.model = str(value).strip()
            elif name == "vendor":
                line.vendor = str(value).strip()
            elif name == "hs":
                line.hs = str(value).strip()
            elif name == "qty":
                fill_qty(line, value, header_is_package=False)
            elif name == "packages":
                if isinstance(value, str) and value.startswith("="):
                    continue
                fill_qty(line, value, header_is_package=True)
            elif name == "price":
                line.price = parse_number(value) if not _formula(value) else None
            elif name == "amount":
                line.amount = parse_number(value) if not _formula(value) else None
            elif name == "gross":
                line.gross = parse_number(value) if not _formula(value) else None
            elif name == "net":
                line.net = parse_number(value) if not _formula(value) else None
        if is_freight(line.description):
            line.freight = True
        if line.pieces is None and line.price and line.amount:
            pass
        useful = any(getattr(line, name) is not None for name in ("pieces", "packages", "price", "amount", "gross", "net"))
        if useful or (line.vendor and line.description):
            lines.append(line)
    return lines


def _formula(value):
    return isinstance(value, str) and value.startswith("=")


def _header(rows):
    best = None
    best_score = 0
    best_idx = None
    for index, row in enumerate(rows[:40]):
        mapped = {}
        score = 0
        for col, cell in enumerate(row):
            name = column_of(cell)
            if not name or name in mapped:
                continue
            mapped[name] = col
            score += 2 if name in {"description", "qty", "vendor", "price"} else 1
        if score > best_score and "description" in mapped and ({"qty", "price", "vendor", "packages"} & set(mapped)):
            best = mapped
            best_score = score
            best_idx = index
    return best_idx, best or {}


def _xlsx(path):
    wb = load_workbook(path, data_only=False)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        last = min(ws.max_row or 1, 80)
        width = min(ws.max_column or 1, 40)
        for r in range(1, last + 1):
            rows.append([ws.cell(r, c).value for c in range(1, width + 1)])
        sheets.append({"name": ws.title, "rows": rows})
    return sheets


def _xls(path):
    if xlrd is None:
        return []
    book = xlrd.open_workbook(path)
    sheets = []
    for sheet in book.sheets():
        rows = []
        for r in range(min(sheet.nrows, 80)):
            rows.append([sheet.cell_value(r, c) for c in range(min(sheet.ncols, 40))])
        sheets.append({"name": sheet.name, "rows": rows})
    return sheets
