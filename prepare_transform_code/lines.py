import re
from dataclasses import dataclass, field

from prepare_transform_code.numbers import (
    collapse_letter_spacing,
    is_package_text,
    is_piece_text,
    package_count,
    parse_number,
)

# Код в начале описания: «SP 085-28 - PODSTAWA…». Номер строки Item / Poz сюда не входит.
_ARTICLE = re.compile(
    r"^([A-Z]{1,4}\s+\d{2,}[A-Z0-9]*(?:-[A-Z0-9]+)*)\s+-\s+",
    re.I,
)

# Слово вида места в колонке единицы: это не единица количества.
_PACKAGE_KIND = {
    "roll", "rolls", "carton", "cartons", "box", "boxes", "ctn", "ctns",
    "package", "packages", "sack", "sacks", "bale", "bales", "pkg", "pkgs",
}


@dataclass
class Line:
    source: str = ""
    description: str = ""
    model: str = ""
    vendor: str = ""
    hs: str = ""
    qty_text: str = ""
    pieces: float | None = None
    packages: float | None = None
    price: float | None = None
    amount: float | None = None
    gross: float | None = None
    net: float | None = None
    net_primary: float | None = None
    volume: float | None = None
    area: float | None = None
    width: float | None = None
    gsm: float | None = None
    gross_with_pallet: float | None = None
    unit_net: float | None = None
    unit: str = ""
    origin: str = ""
    brand: str = ""
    producer: str = ""
    finish: str = ""
    pallet: str = ""
    package_type: str = ""
    note: str = ""
    size: str = ""
    order_ref: str = ""
    hs_alt: str = ""
    measure_group: dict | None = None
    freight: bool = False
    extra: dict = field(default_factory=dict)

    def anchors(self):
        codes = []
        vendor = _norm(self.vendor)
        if vendor and any(ch.isdigit() for ch in vendor) and vendor not in {"--", "na"}:
            codes.append(vendor)
        for code in re.findall(r"\d(?:[a-z0-9]*-\d[a-z0-9]*)+", str(self.vendor or "").lower()):
            codes.append("sku:" + code)
        model = _norm(self.model)
        if model and model not in {"--", "na", "отсутствует"}:
            codes.append("model:" + model)
        desc = _norm(self.description)
        if desc:
            codes.append("desc:" + desc[:48])
        return codes


def _norm(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def is_bare_total(line, previous):
    """Строка без названия, чьё число равно сумме строк выше. Это итог, даже без слова TOTAL."""
    if line.description or line.vendor or line.model:
        return False
    if not previous:
        return False
    if line.amount is not None:
        total = sum(item.amount or 0 for item in previous if not item.freight)
        if total > 0 and abs(total - line.amount) <= max(0.02, abs(total) * 0.002):
            return True
    if line.price is not None:
        return False
    for name in ("pieces", "packages"):
        value = getattr(line, name)
        if value is None:
            continue
        total = sum(getattr(item, name) or 0 for item in previous if not item.freight)
        if total > 0 and abs(total - value) <= max(0.02, abs(total) * 0.002):
            return True
    return False


def is_pallet_only(line):
    """Строка без названия и цены, в которой только паллеты. Это не товар."""
    if line.description or line.price is not None or line.amount is not None or line.vendor:
        return False
    blob = f"{line.qty_text} {line.package_type} {line.pallet}".lower()
    return "pallet" in blob or "паллет" in blob or "палет" in blob


def pull_article(line):
    """Артикул, приклеенный к началу описания через « - », если своей колонки нет."""
    _glue_split_article(line)
    _pull_packages(line)
    _pull_hs(line)
    if line.vendor:
        return
    match = _ARTICLE.match(str(line.description or "").strip())
    if not match:
        return
    code = match.group(1).strip()
    if any(ch.isdigit() for ch in code):
        line.vendor = code


_PACKED = re.compile(r"упаковк\w*\s*:\s*(\d+)", re.I)
_HS_IN_NAME = re.compile(r"(?:код\s*т[нh]\s*вэд|[hн]\s*s\s*code)\s*:?\s*(\d{6,10})", re.I)


def _pull_packages(line):
    """«Упаковка: 111 мешков» внутри наименования — места, отдельной колонки нет."""
    if line.packages is not None:
        return
    found = _PACKED.search(line.description or "")
    if found:
        line.packages = float(found.group(1))


def _pull_hs(line):
    """Код ТН ВЭД, вписанный в наименование, если своей колонки нет."""
    found = _HS_IN_NAME.search(line.description or "")
    if not found or line.hs:
        return
    line.hs = found.group(1)
    line.description = " ".join((line.description[:found.start()] + " " + line.description[found.end():]).split())


def _glue_split_article(line):
    """Хвост артикула уехал в соседнюю колонку: «8710M-40-» и «4Insoles»."""
    vendor = str(line.vendor or "")
    desc = str(line.description or "")
    if not vendor.endswith("-"):
        return
    match = re.match(r"^(\d{1,4})(?=[A-Za-zА-Яа-я])(.*)$", desc)
    if not match:
        return
    line.vendor = vendor + match.group(1)
    line.description = match.group(2).strip()


def hs_text(value):
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return collapse_letter_spacing(str(value).replace("\n", " ").strip())


def _cell_text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return collapse_letter_spacing(" ".join(str(value).replace("\n", " ").split()))


def assign_cell(line, name, value, header_is_package=False):
    if value is None or value == "":
        return
    if isinstance(value, str) and value.startswith("="):
        return
    if name == "description":
        text = " ".join(str(value).replace("\n", " ").split())
        if not text:
            return
        if line.description and text not in line.description:
            line.description = line.description + " // " + text
        elif not line.description:
            line.description = text
        return
    if name in {"model", "vendor", "unit", "origin", "brand", "producer", "finish", "pallet", "package_type", "note", "size", "order_ref"}:
        text = _cell_text(value)
        # Колонка CODE с числом из 8–10 цифр — это ТН ВЭД, не артикул.
        if name == "vendor" and text and not line.hs and re.fullmatch(r"\d{8,10}", text):
            line.hs = text
            return
        if name == "brand" and text in {"-", "—", "–", "/"}:
            return
        # В колонку единицы иногда уезжает название товара. Единица — короткое слово.
        if name == "unit" and (len(text) > 24 or len(text.split()) > 4):
            return
        if name == "unit" and text.casefold() in _PACKAGE_KIND:
            if not line.package_type:
                line.package_type = text
            return
        if text and not getattr(line, name):
            setattr(line, name, text)
        return
    if name == "hs":
        text = hs_text(value)
        if text and not line.hs:
            line.hs = text
        return
    if name == "hs_alt":
        text = hs_text(value)
        if text and not line.hs_alt:
            line.hs_alt = text
        return
    if name == "qty":
        fill_qty(line, value, header_is_package=header_is_package)
        return
    if name == "packages":
        fill_qty(line, value, header_is_package=True)
        return
    if name in {"price", "amount", "gross", "net", "net_primary", "volume", "area", "width", "gsm", "gross_with_pallet", "unit_net"}:
        number = parse_number(value)
        if number is not None and getattr(line, name) is None:
            setattr(line, name, number)


def fill_qty(line, text, header_is_package=False):
    if isinstance(text, str) and text.startswith("="):
        return
    text = str(text).replace("\n", " ").strip()
    if not text or text.startswith("="):
        return
    line.qty_text = (line.qty_text + " " + text).strip()
    packs = package_count(text)
    if packs is not None:
        line.packages = packs if line.packages is None else line.packages + packs
        return
    if is_piece_text(text):
        number = parse_number(text)
        if number is not None and line.pieces is None:
            line.pieces = number
        return
    number = parse_number(text)
    if number is None:
        return
    if header_is_package or is_package_text(text):
        line.packages = number
    elif line.pieces is None:
        line.pieces = number


def part_key(line):
    """Одно изделие, разложенное по контейнерам. Пустой ключ не склеивает чужие строки."""
    if getattr(line, "freight", False):
        return ""
    model = re.sub(r"[^a-z0-9]+", "", collapse_letter_spacing(getattr(line, "model", "") or "").lower())
    hs = re.sub(r"\D", "", collapse_letter_spacing(getattr(line, "hs", "") or ""))
    if model and hs:
        return model + "|" + hs
    if model:
        return "m:" + model
    if hs and not (getattr(line, "vendor", "") or "").strip():
        return "h:" + hs
    return ""


def fold_parts(lines):
    """Строки одного изделия без своего количества и своей цены — части, не отдельные лоты."""
    folded = []
    index = 0
    while index < len(lines):
        line = lines[index]
        key = part_key(line)
        group = [line]
        cursor = index + 1
        while key and cursor < len(lines) and part_key(lines[cursor]) == key and not line.freight:
            group.append(lines[cursor])
            cursor += 1
        if len(group) == 1 or not _can_fold(group):
            folded.append(line)
            index += 1
            continue
        folded.append(_collapse_group(group))
        index = cursor
    return folded


def _can_fold(group):
    if any(line.freight for line in group):
        return False
    if sum(line.pieces is not None for line in group) > 1:
        return False
    if sum(line.amount is not None for line in group) > 1:
        return False
    if sum(line.price is not None for line in group) > 1:
        return False
    return True


def _word_count(text):
    return sum(1 for word in str(text or "").split() if len(word) > 1)


def _plain_score(text):
    return sum(len(word) for word in str(text or "").split() if len(word) > 1)


def _letters(text):
    return re.sub(r"[^0-9a-zа-яё]+", "", str(text or "").lower())


def _squash(text):
    return re.sub(r"[^a-z0-9]+", "", collapse_letter_spacing(str(text or "")).lower())


def _is_contents(text):
    low = str(text or "").lower()
    return low.count("package") >= 2


def _product_title(text):
    """Перечень Package 1, Package 2 — содержимое, не второе название."""
    raw = str(text or "").strip()
    if not _is_contents(raw):
        return raw
    title = re.split(r"package\s*1\b", raw, maxsplit=1, flags=re.I)[0].strip(" :/")
    return title


def _prefer_description(current, new):
    return _prefer_text(_product_title(current), _product_title(new))


def _prefer_text(current, new):
    current = current or ""
    new = new or ""
    if not new:
        return current
    if not current:
        return new
    if _squash(current) == _squash(new):
        return new if _word_count(new) > _word_count(current) else current
    return new if _plain_score(new) > _plain_score(current) else current


def _collapse_group(group):
    head = group[0]
    for line in group:
        head.description = _prefer_description(head.description, line.description)
        head.model = _prefer_text(head.model, line.model)
        if line.hs and (not head.hs or " " in head.hs):
            head.hs = line.hs
        if line.origin and (not head.origin or " " in head.origin):
            head.origin = line.origin
        for name in ("pieces", "amount", "price", "vendor", "unit", "producer", "brand"):
            if getattr(head, name) in (None, "") and getattr(line, name) not in (None, ""):
                setattr(head, name, getattr(line, name))
    for name in ("packages", "gross", "net", "volume"):
        values = [getattr(line, name) for line in group if getattr(line, name) is not None]
        if values:
            setattr(head, name, sum(values))
    return head
