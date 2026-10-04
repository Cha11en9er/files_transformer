import re

import pdfplumber

from prepare_transform_code.fields import (
    column_of,
    is_header_row,
    is_row_index,
    is_shipper_code,
    is_stop_label,
    role_of,
    roles_of,
    stamp_header,
)
from prepare_transform_code.lines import (
    Line,
    assign_cell,
    attach_pallet,
    fold_parts,
    is_bare_total,
    is_pallet_only,
    pull_article,
    settle_pallets,
)
from prepare_transform_code.numbers import (
    _COMMA_IS_DECIMAL,
    _DOT_IS_DECIMAL,
    collapse_letter_spacing,
    collapse_overprint,
    comma_is_decimal,
    currency_of,
    dot_is_decimal,
    parse_number,
    undouble_row,
)


def read_pdf(path):
    pages = []
    found_lines = []
    stated = {}
    inherited = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = collapse_overprint(page.extract_text() or "")
            words = page.extract_words() or []
            tables = []
            page_found = []
            dot_token = _DOT_IS_DECIMAL.set(dot_is_decimal(text))
            comma_token = _COMMA_IS_DECIMAL.set(comma_is_decimal(text))
            try:
                for table in page.find_tables() or []:
                    data = table.extract() or []
                    tables.append(data)
                    page_lines, inherited, page_stated = _table_lines(
                        data, _row_boxes(table), words, table.bbox, inherited
                    )
                    page_found.extend(page_lines)
                    stated = _merge_stated(stated, page_stated)
                if not page_found:
                    page_found = _borderless(words)
            finally:
                _COMMA_IS_DECIMAL.reset(comma_token)
                _DOT_IS_DECIMAL.reset(dot_token)
            found_lines.extend(page_found)
            stated = _merge_stated(stated, stated_from_text(text))
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
    side = _side_cells(pages)
    if side:
        raw = side + "\n" + raw
    text = collapse_letter_spacing(raw)
    readable = _readable(text)
    lines = fold_parts(_share_measures(found_lines)) if readable else []
    settle_pallets(lines)
    role = role_of(text) if readable else "scan"
    roles = roles_of(text) if readable else []
    if role not in roles and role not in {"unknown", "scan"}:
        roles.insert(0, role)
    if role == "unknown" and _looks_like_packing(lines):
        role = "packing"
        roles = ["packing"]
    return {
        "kind": "pdf",
        "text": text,
        "raw_text": raw,
        "pages": len(pages),
        "rotation": pages[0]["rotation"] if pages else 0,
        "images": sum(page["images"] for page in pages),
        "chars": sum(page["chars"] for page in pages),
        "readable": readable,
        "role": role,
        "roles": roles,
        "currency": currency_of(text),
        "lines": lines,
        "stated": {key: value for key, value in stated.items() if value is not None},
        "tables": [table for page in pages for table in page["tables"]],
    }


def _readable(text):
    upper = text.upper()
    marks = (
        "INVOICE", "PACKING", "DESCRIPTION", "QTY", "СПЕЦИФИКАЦИЯ", "ИНВОЙС",
        "АРТИКУЛ", "ОПИСАНИЕ", "КОЛ-ВО", "RECHNUNG", "FACTURE", "FACTURA",
        "CONTENTS", "MENGE", "BEZEICHNUNG",
    )
    return any(mark in upper for mark in marks)


def _looks_like_packing(lines):
    return any(line.packages and (line.net is not None or line.gross is not None) for line in lines)


def _row_boxes(table):
    boxes = []
    for row in table.rows:
        boxes.append(getattr(row, "bbox", None))
    return boxes


def _role(text):
    return role_of(text)


def _side_cells(pages):
    """Клетки шапки, где продавец и покупатель стоят рядом. Плоский текст их склеивает."""
    lines = []
    for page in pages:
        for table in page["tables"]:
            if _goods_table(table):
                continue
            for row in table:
                for cell in row or []:
                    text = " ".join(str(cell or "").replace("\n", " ").split())
                    if text:
                        lines.append(text)
    return "\n".join(lines)


def _goods_table(table):
    for row in table[:6]:
        hits = {column_of(cell) for cell in row or []}
        if "description" in hits and ({"qty", "packages", "price"} & hits):
            return True
    return False


def _map_row(row):
    mapped = {}
    extra = []
    for col, cell in enumerate(row):
        name = column_of(cell)
        if not name:
            continue
        if name == "description" and "description" in mapped:
            extra.append(col)
            continue
        if name == "vendor" and "vendor" in mapped and "model" not in mapped:
            if not stamp_header(cell):
                mapped["model"] = col
            continue
        if name == "hs" and is_shipper_code(cell):
            # Код поставщика — второй код, даже если колонка стоит левее ТН ВЭД.
            if "hs_alt" not in mapped:
                mapped["hs_alt"] = col
                mapped["_hs_alt_shipper"] = "1"
            continue
        if name == "hs" and "hs" in mapped and "hs_alt" not in mapped:
            mapped["hs_alt"] = col
            continue
        if name not in mapped:
            mapped[name] = col
    if "hs" not in mapped and "hs_alt" in mapped and mapped.get("_hs_alt_shipper"):
        mapped["hs"] = mapped.pop("hs_alt")
        mapped.pop("_hs_alt_shipper", None)
    if extra:
        mapped["_description_extra"] = extra
    return mapped


def _table_lines(table, boxes, words, table_box, inherited):
    header_idx = None
    mapping = {}
    for index, row in enumerate(table):
        mapped = _map_row(row)
        if {"description", "qty"} & set(mapped) and len(mapped) >= 2:
            header_idx = index
            mapping = mapped
            break
    if header_idx is None:
        if not inherited or not table or not _same_width(table, inherited):
            return [], inherited, {}
        if any(is_stop_label(cell) for cell in table[0] if cell):
            return [], inherited, _stated_from_row(table[0], inherited)
        mapping = dict(inherited)
        cursor = 0
    else:
        cursor = header_idx + 1
    while cursor < len(table) and _header_continuation(table[cursor]):
        for name, col in _map_row(table[cursor]).items():
            if name.startswith("_"):
                continue
            existing = mapping.get(name)
            if existing is None:
                mapping[name] = col
                continue
            if existing == col:
                continue
            # Ключ уже занят другой колонкой, а эта колонка подписана ещё раз: ART. рядом с MODEL.
            used = [key for key, val in mapping.items() if val == col and not str(key).startswith("_")]
            if not used:
                continue
            aliases = mapping.setdefault("_alias", {})
            aliases.setdefault(col, [])
            if name not in aliases[col] and name not in used:
                aliases[col].append(name)
        cursor += 1
    lines = []
    stated = {}
    for offset, row in enumerate(table[cursor:]):
        row = undouble_row(row)
        if is_header_row(row):
            continue
        if any(is_stop_label(cell) for cell in row if cell):
            stated = _merge_stated(stated, _stated_from_row(row, mapping))
            break
        line = Line(source="pdf")
        shared = _shared_columns(mapping)
        for name, col in mapping.items():
            if name.startswith("_") or not isinstance(col, int) or col >= len(row):
                continue
            if col in shared:
                continue
            header_cell = ""
            if header_idx is not None and header_idx < len(table) and col < len(table[header_idx]):
                header_cell = table[header_idx][col]
            if name == "vendor" and is_row_index(header_cell, row[col]):
                continue
            assign_cell(line, name, row[col], header_is_package=(mapping.get("packages") == col))
        unit_col = mapping.get("unit")
        mapped_cols = {col for key, col in mapping.items() if isinstance(col, int)}
        if (
            isinstance(unit_col, int)
            and line.package_type
            and line.packages is None
            and unit_col + 1 < len(row)
            and unit_col + 1 not in mapped_cols
        ):
            count = parse_number(row[unit_col + 1])
            if count is not None:
                line.packages = count
        for col, names in shared.items():
            if col >= len(row):
                continue
            _assign_shared(line, names, row[col])
        for col in mapping.get("_description_extra") or []:
            if col < len(row):
                assign_cell(line, "description", row[col])
        if line.hs_alt and mapping.get("_hs_alt_shipper"):
            line.hs_alt_shipper = True
        pull_article(line)
        if not line.unit and _kg_price(table, mapping, header_idx, cursor):
            line.unit = "kg"
        if not line.unit and _pcs_header(table, mapping, header_idx, cursor):
            line.unit = _pcs_header(table, mapping, header_idx, cursor)
        if not line.unit and _meter_header(table, mapping, header_idx, cursor):
            line.unit = "meters"
        note = _side_note(words, boxes[cursor + offset] if cursor + offset < len(boxes) else None, table_box)
        if note:
            line.note = note
            if _pallet_note(note):
                line.pallet = note
        measured = any(
            getattr(line, name) is not None
            for name in ("pieces", "packages", "price", "amount", "gross", "net", "net_primary", "gross_with_pallet")
        )
        # Вторая модель на общем весе чисел не имеет, но это строка товара.
        # «Purchase contract» и дата контракта — нет.
        named = bool(line.model) or (
            any(ch.isdigit() for ch in line.vendor) and bool(line.description)
        )
        if is_bare_total(line, lines):
            break
        if is_pallet_only(line):
            attach_pallet(lines, line)
            continue
        if measured or named:
            lines.append(line)
    return lines, mapping, stated


def _same_width(table, mapping):
    width = max((len(row) for row in table), default=0)
    indexes = [col for key, col in mapping.items() if isinstance(col, int)]
    indexes.extend(mapping.get("_description_extra") or [])
    if not indexes:
        return False
    return abs(width - (max(indexes) + 1)) <= 2


def _side_note(words, row_box, table_box):
    """Текст справа от нарисованной сетки, на одной высоте со строкой, всё ещё её поле."""
    if not row_box or not table_box or not words:
        return ""
    right = table_box[2] - 1
    y0, y1 = row_box[1], row_box[3]
    picked = []
    for word in words:
        if word["x0"] < right:
            continue
        mid = (word["top"] + word["bottom"]) / 2
        if mid < y0 - 2 or mid > y1 + 2:
            continue
        picked.append(word)
    picked.sort(key=lambda item: item["x0"])
    return " ".join(item["text"] for item in picked).strip()


def _pallet_note(text):
    low = text.lower()
    return "пал" in low or "pallet" in low or "част" in low


def _pcs_header(table, mapping, header_idx, cursor):
    """«QTY PCS» — штуки, отдельной колонки единицы нет. Несколько единиц в шапке не выбираем."""
    col = mapping.get("qty")
    if not isinstance(col, int):
        return ""
    start = 0 if header_idx is None else header_idx
    for row in table[start:cursor]:
        if col >= len(row or []):
            continue
        text = collapse_letter_spacing(" ".join(str(row[col] or "").lower().split()))
        if re.search(r"pair|пар|set|pcg|компл", text):
            return ""
        if re.search(r"\bpcs\b|pcs|штук", text):
            return "шт" if "штук" in text and "pcs" not in text else "pcs"
    return ""


def _meter_header(table, mapping, header_idx, cursor):
    """QUANTITY(MT) — метры, отдельной колонки единицы нет. MT2 — площадь, не метры."""
    col = mapping.get("qty")
    if not isinstance(col, int):
        return False
    start = 0 if header_idx is None else header_idx
    for row in table[start:cursor]:
        if col >= len(row or []):
            continue
        text = collapse_letter_spacing(" ".join(str(row[col] or "").lower().split()))
        compact = text.replace(" ", "")
        if "mt2" in compact or "m2" in compact:
            continue
        if re.search(r"meters?|metres?|(?<![a-z])mt(?![a-z])", text):
            return True
    return False


def _kg_price(table, mapping, header_idx, cursor):
    """«Цена за кг» — единица кг, отдельной колонки единицы нет."""
    col = mapping.get("price")
    if not isinstance(col, int):
        return False
    start = 0 if header_idx is None else header_idx
    for row in table[start:cursor]:
        if col >= len(row or []):
            continue
        text = " ".join(str(row[col] or "").lower().split())
        if re.search(r"за\s*кг|per\s*kg|1\s*kg|/kg", text):
            return True
    return False


def _shared_columns(mapping):
    """Две подписи одной колонки: MODEL и ART., либо MANUFACTURER и BRAND."""
    found = {}
    for name, col in mapping.items():
        if str(name).startswith("_") or not isinstance(col, int):
            continue
        found.setdefault(col, [])
        if name not in found[col]:
            found[col].append(name)
    for col, names in (mapping.get("_alias") or {}).items():
        found.setdefault(col, [])
        for name in names:
            if name not in found[col]:
                found[col].append(name)
    return {col: names for col, names in found.items() if len(names) >= 2}


def _blank_brand(text):
    token = " ".join(str(text or "").casefold().split())
    return token in {"-", "—", "–", "/", "no brand", "n/a", "n/m"} or token.startswith("отсутств")


def _assign_shared(line, names, value):
    """Один столбец, две подписи. Слэш делит клетку. Без слэша одно число — и модель, и артикул."""
    text = " ".join(str(value or "").replace("\n", " ").split())
    if not text:
        return
    parts = [part.strip() for part in re.split(r"\s*/\s*", text) if part.strip()]
    if len(parts) >= 2:
        paired = list(zip(names, parts))
        if len(parts) > len(names):
            paired[-1] = (names[-1], " / ".join(parts[len(names) - 1 :]))
        for name, part in paired:
            if name == "brand" and _blank_brand(part):
                continue
            assign_cell(line, name, part)
        return
    if set(names) <= {"model", "vendor"}:
        for name in names:
            assign_cell(line, name, text)
        return
    assign_cell(line, names[0], text)


def _header_continuation(row):
    mapped = _map_row(row)
    if not mapped:
        return False
    if any(is_stop_label(cell) for cell in row if cell):
        return False
    # Слова единицы и вида места рядом с названием и числом — строка товара, не вторая шапка.
    if _goods_name(row) and any(re.search(r"\d", str(cell or "")) for cell in row):
        return False
    if any(cell and len(str(cell)) > 40 for cell in row):
        return False
    labels = [name for name in mapped if not str(name).startswith("_")]
    if len(labels) < 2 and any(re.search(r"\d", str(cell or "")) for cell in row):
        return False
    return True


def _goods_name(row):
    for cell in row:
        text = " ".join(str(cell or "").replace("\n", " ").split())
        if len(text) <= 8 or column_of(text):
            continue
        if re.search(r"[A-Za-zА-Яа-яЁё]", text):
            return True
    return False


def _stated_from_row(row, mapping):
    line = Line(source="pdf")
    for name, col in mapping.items():
        if name.startswith("_") or not isinstance(col, int) or col >= len(row):
            continue
        assign_cell(line, name, row[col], header_is_package=(mapping.get("packages") == col))
    out = {}
    for source, target in (
        ("pieces", "pieces"),
        ("packages", "packages"),
        ("amount", "amount"),
        ("net", "net"),
        ("gross", "gross"),
        ("area", "area"),
    ):
        value = getattr(line, source)
        if value is not None:
            out[target] = value
    return out


def stated_from_text(text):
    """Итог, который остался под таблицей и в сетку клеток не попал."""
    out = {}
    weight = re.search(
        r"total\s+weight\s*:?\s*([\d.,]+)\s*kgs?\s*net\s*/\s*([\d.,]+)\s*kgs?\s*gross",
        text or "",
        re.I,
    )
    if weight:
        net = parse_number(weight.group(1))
        gross = parse_number(weight.group(2))
        if net is not None:
            out["net"] = net
        if gross is not None:
            out["gross"] = gross
    for line in (text or "").splitlines():
        match = re.fullmatch(
            r"\s*([\d][\d.,]*)\s+met(?:er|re)s?\s+rolls?\s+([\d][\d.,]*)\s+([\d][\d.,]*)\s*",
            line,
            re.I,
        )
        if not match:
            continue
        pieces = parse_number(match.group(1))
        packages = parse_number(match.group(2))
        amount = parse_number(match.group(3))
        if pieces is not None:
            out["pieces"] = pieces
        if packages is not None:
            out["packages"] = packages
        if amount is not None:
            out["amount"] = amount
    return out


def _merge_stated(left, right):
    out = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            continue
        current = out.get(key)
        if current is None:
            out[key] = value
        elif abs(current - value) > 0.05:
            out[key] = None
    return out


def _borderless(words):
    """Таблица без линий. Слова одной строки стоят на одной высоте, колонки — друг под другом."""
    bands = _bands(words)
    header_at = None
    columns = None
    best = 0
    for index, band in enumerate(bands):
        cols = _header_columns(band)
        names = {col["name"] for col in cols if col["name"]}
        identity = {"description", "vendor", "model"} & names or any(_slash_header(col["text"]) for col in cols)
        measure = {"qty", "price", "packages", "amount", "net", "gross"} & names
        if identity and measure and len(names) > best:
            best = len(names)
            header_at = index
            columns = cols
    if header_at is None or not columns:
        return []
    lines = []
    for band in bands[header_at + 1 :]:
        if any(is_stop_label(word["text"]) for word in band):
            break
        line = _band_line(band, columns)
        if line is None:
            continue
        measured = any(
            getattr(line, name) is not None
            for name in ("pieces", "packages", "price", "amount", "gross", "net")
        )
        if measured:
            lines.append(line)
    if not _amounts_match(lines) or not _named_goods(lines):
        return []
    return lines


def _named_goods(lines):
    """Строка без артикула годится, если рядом описание и цена или вес."""
    named = 0
    for line in lines:
        if line.vendor:
            named += 1
        elif line.description and (line.price or line.net or line.gross or line.packages):
            named += 1
    return named >= max(1, len(lines) // 2)


def _amounts_match(lines):
    """Цена и сумма в строке должны сходиться. Иначе это не та таблица."""
    checked = [line for line in lines if line.price and line.amount and line.pieces]
    if not checked:
        return True
    good = 0
    for line in checked:
        if abs(line.price * line.pieces - line.amount) <= max(0.1, abs(line.amount) * 0.02):
            good += 1
    return good >= max(1, len(checked) // 2)


def _bands(words):
    fine = []
    for word in sorted(words or [], key=lambda item: (item["top"], item["x0"])):
        if fine and abs(word["top"] - fine[-1][0]["top"]) <= 2.5:
            fine[-1].append(word)
        else:
            fine.append([word])
    bands = []
    for row in fine:
        if bands and row[0]["top"] - bands[-1][-1]["top"] <= 6:
            bands[-1].extend(row)
        else:
            bands.append(list(row))
    for band in bands:
        band.sort(key=lambda item: item["x0"])
    return bands


def _header_columns(band):
    columns = []
    for word in band:
        named = column_of(word["text"])
        measure = named in {"qty", "price", "amount", "packages", "net", "gross", "area"} or bool(
            re.fullmatch(r"total", word["text"].strip(), re.I)
        )
        previous = columns[-1]["text"] if columns else ""
        in_slash = " /" in previous or previous.endswith("/")
        close = columns and word["x0"] - columns[-1]["x1"] <= 16
        if close and not measure and (in_slash or not named):
            columns[-1]["x1"] = max(columns[-1]["x1"], word["x1"])
            columns[-1]["text"] = (columns[-1]["text"] + " " + word["text"]).strip()
            continue
        columns.append({"text": word["text"], "x0": word["x0"], "x1": word["x1"], "name": None})
    for col in columns:
        col["name"] = column_of(col["text"])
        if not col["name"] and re.fullmatch(r"total", col["text"].strip(), re.I):
            col["name"] = "amount"
        if not col["name"] and _slash_header(col["text"]):
            col["name"] = "description"
    cleaned = []
    for col in columns:
        if cleaned and col["x0"] <= cleaned[-1]["x1"] + 6:
            if col["name"] and not cleaned[-1]["name"]:
                cleaned[-1] = col
                continue
            if not col["name"]:
                continue
        cleaned.append(col)
    return cleaned


def _slash_header(text):
    labels = [part.strip() for part in re.split(r"\s*/\s*", str(text or "")) if part.strip()]
    return len(labels) >= 3 and any(re.search(r"design|desing|item|code", part, re.I) for part in labels)


def _spans(columns):
    spans = []
    for index, col in enumerate(columns):
        if index == 0:
            left = 0
        else:
            left = (columns[index - 1]["x1"] + col["x0"]) / 2
        if index + 1 == len(columns):
            right = 10000
        else:
            right = (col["x1"] + columns[index + 1]["x0"]) / 2
        spans.append({**col, "left": left, "right": right})
    return spans


def _clusters(band):
    groups = []
    for word in band:
        if groups and word["x0"] - groups[-1]["x1"] <= 12:
            groups[-1]["x1"] = max(groups[-1]["x1"], word["x1"])
            groups[-1]["words"].append(word["text"])
            continue
        groups.append({"x0": word["x0"], "x1": word["x1"], "words": [word["text"]]})
    return groups


def _band_line(band, columns):
    centers = [((col["x0"] + col["x1"]) / 2, index) for index, col in enumerate(columns)]
    buckets = {index: [] for index in range(len(columns))}
    for word in band:
        mid = (word["x0"] + word["x1"]) / 2
        index = min(centers, key=lambda item: abs(item[0] - mid))[1]
        buckets[index].append(word["text"])
    line = Line(source="pdf")
    for index, col in enumerate(columns):
        text = " ".join(buckets[index]).strip()
        if not text or not col["name"]:
            continue
        if col["name"] == "vendor" and is_row_index(col["text"], text):
            continue
        assign_cell(line, col["name"], text, header_is_package=(col["name"] == "packages"))
        if col["name"] == "price" and line.amount is None:
            nums = [parse_number(tok) for tok in re.findall(r"\d[\d.,]*", text)]
            nums = [num for num in nums if num is not None]
            if len(nums) >= 2:
                line.price = nums[0]
                line.amount = nums[1]
        if _slash_header(col["text"]):
            _apply_slash(line, col["text"], text)
        if col["name"] == "packages" and re.search(r"roll|рулон", col["text"], re.I):
            line.package_type = line.package_type or "ROLLS"
        if col["name"] == "qty" and re.search(r"\b(mt|mts|meter|metre)\b", f"{col['text']} {text}", re.I):
            if "m2" not in col["text"].lower() and "mt2" not in col["text"].lower():
                line.unit = line.unit or "meters"
    blob = " ".join(word["text"] for word in band)
    if line.pieces is not None and re.search(r"\b(mt|mts|meters?)\b", blob, re.I):
        line.unit = line.unit or "meters"
    _repair_color_tail(line)
    pull_article(line)
    return line


def _repair_color_tail(line):
    """Код цвета и артикул стоят вплотную. Хвост «DYER 490» после кода с точкой — артикул."""
    line.finish = re.sub(r"(\d)([A-Za-zА-Яа-яЁё])", r"\1 \2", str(line.finish or ""))
    finish = " ".join(line.finish.split())
    vendor = " ".join(str(line.vendor or "").split())
    parts = finish.split()
    if re.fullmatch(r"\d+", vendor) and parts and parts[-1].isalpha():
        line.vendor = f"{parts[-1]} {vendor}"
        line.finish = " ".join(parts[:-1])
        return
    if vendor or len(parts) < 4:
        return
    if not any("." in part for part in parts[:-2]):
        return
    if not any(ch.isdigit() for ch in parts[-1]):
        return
    line.vendor = f"{parts[-2]} {parts[-1]}"
    line.finish = " ".join(parts[:-2])


def _apply_slash(line, header, value):
    """«Design No / Design Name / Code / Item / PO» и значение через те же слэши."""
    labels = [part.strip() for part in re.split(r"\s*/\s*", header) if part.strip()]
    values = [part.strip() for part in re.split(r"\s*/\s*", value) if part.strip()]
    if len(labels) < 3 or len(values) < 2:
        return
    pairs = list(zip(labels, values))
    if len(values) > len(labels):
        pairs[-1] = (labels[-1], " / ".join(values[len(labels) - 1 :]))
    for label, item in pairs:
        field = _slash_field(label)
        if not field or not item or item in {"0", "-"}:
            continue
        if field == "vendor":
            line.vendor = item
        elif field == "model" and not line.model:
            line.model = item
        elif field == "finish" and not line.finish:
            line.finish = item
        elif field == "order_ref":
            line.order_ref = item
        elif not getattr(line, field, None):
            assign_cell(line, field, item)


def _slash_field(label):
    low = label.lower().replace("desing", "design")
    if "design name" in low:
        return "vendor"
    if "design no" in low or "design nr" in low or "design #" in low:
        return "model"
    if re.search(r"\bpo\b", low):
        return "order_ref"
    if "item" in low:
        return ""
    if "hs" in low or "customs" in low or "тамож" in low:
        return "hs"
    if low.endswith("code") or "color" in low or "colour" in low:
        return "finish"
    return column_of(label) or ""


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
