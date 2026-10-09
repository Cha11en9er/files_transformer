import hashlib
import re
from pathlib import Path

from prepare_transform_code.exceldoc import read_excel
from prepare_transform_code.join import build_lots
from prepare_transform_code.fields import LOT_FIELDS, is_factory_list, party_after, same_company
from prepare_transform_code.numbers import collapse_letter_spacing, currencies_of, currency_of, goods_currency
from prepare_transform_code.pdfdoc import read_pdf

_INVOICE_NO = re.compile(
    r"(?:INV(?:OICE)?\s*\.?\s*(?:NO|NR|NUMBER)\.?(?:\s+AND\s+DATE)?|(?:NO|NR)\.?\s*INVOICE|(?<![A-Z])INVOICE\s*:)\s*:?\s*([A-Z0-9][A-Z0-9./\-]{2,})",
    re.I,
)
_CONTRACT = re.compile(
    r"(?:CONTRACT|CONTRAT)\s*(?:NO|NR|NUMBER|#|№|N[°º])?\.?\s*:?\s*([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9./\-]{2,})",
    re.I,
)
_CONTRACT_SKIP = {"your", "date", "the", "and", "for", "from", "page", "reference"}
_INVOICE_LABEL = re.compile(r"inv(?:oice)?\s*\.?\s*(?:no|nr|number|n[°º])", re.I)
_NO_BEFORE_TITLE = re.compile(
    r"No\s*:\s*([A-Z]{2,}(?:\s*[-/]\s*[A-Z0-9]+)+(?:\s+REG)?).{0,160}INVOICE",
    re.I | re.S,
)
_ORDER_LABEL = re.compile(r"order[\s\-]*number", re.I)
_ORDER = re.compile(
    r"\bORDERS?\s*:?\s*([A-Z0-9][A-Z0-9_./\-]{4,})",
    re.I,
)


def analyze(folder):
    folder = Path(folder)
    paths = [
        path
        for path in sorted(folder.iterdir(), key=lambda item: item.name.lower())
        if path.is_file()
        and not path.name.startswith("~$")
        and path.suffix.lower() in {".pdf", ".xls", ".xlsx", ".xlsm", ".jpg", ".jpeg", ".png"}
        and not _output_book_name(path.name)
    ]
    documents = []
    seen = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            documents.append(
                {
                    "name": path.name,
                    "role": "duplicate",
                    "duplicate_of": seen[digest],
                    "lines": [],
                    "text": "",
                    "currency": None,
                }
            )
            continue
        seen[digest] = path.name
        if path.suffix.lower() == ".pdf":
            doc = read_pdf(path)
            doc["name"] = path.name
            documents.append(doc)
        elif path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            documents.append(
                {
                    "kind": "image",
                    "name": path.name,
                    "role": "image",
                    "lines": [],
                    "text": "",
                    "readable": False,
                    "currency": None,
                }
            )
        else:
            for sheet in read_excel(path):
                sheet["name"] = f"{path.name} / {sheet['sheet']}"
                documents.append(sheet)

    invoice = _pick(documents, "invoice", prefer_pdf=True)
    if invoice is None:
        invoice = _pick(documents, "proforma", prefer_pdf=True)
    specification = _pick(documents, "specification", prefer_pdf=False)
    description = _pick(documents, "description", prefer_pdf=True)
    # Лист с #REF! и без чисел не база, если рядом есть таблица с количеством и ценой.
    if invoice is not None and not _has_measures(invoice):
        if specification is not None and _has_measures(specification):
            invoice = None
    packings = [doc for doc in documents if _is_role(doc, "packing") and doc["lines"]]
    weight_conflict = len(packings) > 1 and _weights_differ(packings)
    stated = _stated_totals(documents)
    packing_lines = _packing_lines(packings, weight_conflict, stated)
    base = invoice["lines"] if invoice else (specification["lines"] if specification else [])
    spec_lines = []
    if invoice is not None and specification is not None and specification is not invoice:
        spec_lines = list(specification["lines"])
    # Файл описания с заводом и кодом по строкам — тот же набор лотов, не вторая база.
    if description is not None and description is not specification and description.get("lines"):
        spec_lines.extend(description["lines"])
    foreign = _companions(documents, invoice, specification, description, base, spec_lines)
    lots, freights = build_lots(base, packing_lines, spec_lines)
    flags = []
    if weight_conflict:
        flags.append("weight_conflict")
    if foreign:
        flags.append("foreign_document")
    if any(lot.get("unit_conflict") for lot in lots):
        flags.append("unit_conflict")
    if any(lot.get("hs_alt") for lot in lots):
        flags.append("hs_conflict")
    if any(lot.get("packages_conflict") for lot in lots):
        flags.append("packages_conflict")
    anchors = _header_anchors(documents)
    base_vendors = _vendor_set(base)
    header_docs = [
        doc
        for doc in documents
        if _counts_for_header(doc, anchors) and _same_goods_sheet(doc, base_vendors)
    ]
    plain = "\n".join((doc.get("raw_text") or doc.get("text") or "") for doc in header_docs)
    text = collapse_letter_spacing(plain)
    delivery, delivery_conflict = _delivery(plain)
    if delivery_conflict:
        flags.append("delivery_conflict")
    payment, _payment_conflict = _payment(plain)
    bank_line = _bank_line(plain)
    if len(_label_hits(plain, r"(?:manufacturer|manufactured(?:\s+by)?|производитель|произведено)\s*:")) > 1:
        flags.append("manufacturer_conflict")
    currencies = [
        doc.get("currency")
        for doc in header_docs
        if doc.get("currency") and doc.get("readable", True)
    ]
    all_currencies = []
    for code in currencies + currencies_of(text):
        if code not in all_currencies:
            all_currencies.append(code)
    notes = []
    if len(all_currencies) > 1 and not goods_currency(text):
        notes.append("Валюта не привязана к колонке цены, в документах несколько кодов: " + ", ".join(all_currencies))
    invoice_nos = _labeled_numbers(plain, _INVOICE_LABEL)
    invoice_no = _first(_INVOICE_NO, text)
    if invoice_no and invoice_no not in invoice_nos:
        invoice_nos.insert(0, invoice_no)
    elif invoice_no is None and invoice_nos:
        invoice_no = invoice_nos[0]
    if invoice_no is None:
        invoice_no = _first(_NO_BEFORE_TITLE, text)
        if invoice_no and invoice_no not in invoice_nos:
            invoice_nos.insert(0, invoice_no)
    parties = party_after(plain)
    for side in ("seller", "buyer", "consignee"):
        parties[side] = _party_head(parties.get(side))
    if not parties.get("seller"):
        letterhead = _party_head(_letterhead_seller(plain) or _vendor_company(plain))
        if letterhead:
            parties["seller"] = letterhead
    if not parties.get("buyer"):
        addressed = _party_head(_to_company(plain))
        if addressed:
            parties["buyer"] = addressed
    origin = _one_label(plain, r"(?:country\s+of\s+origin|origin(?:\s+of\s+goods)?)\s*:") or _of_origin(plain)
    producer = _one_label(plain, r"(?:manufacturer|manufactured(?:\s+by)?|производитель|произведено)\s*:")
    if is_factory_list(producer):
        producer = ""
    seller_address, buyer_address, consignee_address = _address_blocks(plain)
    if not buyer_address:
        buyer_address = _party_continuation(plain, parties.get("buyer") or "")
    ship_to = _ship_to_address(plain)
    if ship_to and not buyer_address:
        buyer_address = ship_to
    if not seller_address:
        seller_address = _letterhead_address(plain, parties.get("seller") or "")
    seller_address = _clean_address(_complete_address(seller_address, plain))
    buyer_address = _clean_address(_complete_address(buyer_address, plain))
    buyer_address = _drop_foreign_country(buyer_address, seller_address)
    for lot in lots:
        if _weight_suspect(lot, freights):
            lot["weight_suspect"] = True
    if any(lot.get("weight_suspect") for lot in lots):
        flags.append("weight_suspect")
    if any(lot.get("conflicts") for lot in lots):
        flags.append("values_conflict")
    own_producer = any(
        lot.get("producer") and not is_factory_list(lot.get("producer")) for lot in lots
    )
    for lot in lots:
        if origin and not lot.get("origin"):
            lot["origin"] = origin
        if (
            producer
            and not lot.get("producer")
            and not own_producer
            and not same_company(producer, parties.get("seller"))
        ):
            lot["producer"] = producer
    proforma_nos = _proforma_nos(documents)
    if _packages_miss_stated(lots, stated):
        flags.append("packages_gap")
    invoice_date = _labeled_date(plain) or _spaced_date(plain)
    invoice_seen = any(_is_role(doc, "invoice") and doc.get("readable", True) for doc in documents)
    if invoice_date and invoice is None and not invoice_seen:
        notes.append(
            "Инвойс код не прочитал (скан или нет файла), дата инвойса взята из других документов и может быть датой спецификации."
        )
    return {
        "notes": notes,
        "documents": [
            {
                "name": doc["name"],
                "role": doc["role"],
                "roles": doc.get("roles") or ([doc["role"]] if doc.get("role") else []),
                "duplicate_of": doc.get("duplicate_of"),
                "line_count": len(doc.get("lines") or []),
            }
            for doc in documents
        ],
        "lots": lots,
        "spec_rows": _spec_rows(specification),
        "document_tables": {doc["name"]: _document_table(doc) for doc in documents},
        "freights": [_plain(line) for line in freights],
        "flags": flags,
        "currency": goods_currency(text) or (currencies[0] if currencies else currency_of(text)),
        "currency_printed": _printed_currency(goods_currency(text) or (currencies[0] if currencies else currency_of(text)), text),
        "currencies": all_currencies,
        "invoice_no": invoice_no,
        "invoice_nos": invoice_nos,
        "proforma_no": proforma_nos[0] if proforma_nos else None,
        "proforma_nos": proforma_nos,
        "order_no": _order_no(text),
        "order_nos": _labeled_numbers(plain, _ORDER_LABEL),
        "contract": _contract(plain),
        "contract_date": _contract_date(plain),
        "invoice_date": invoice_date,
        "delivery": delivery,
        "container": _container(plain),
        "director": _director(plain),
        "seller": parties.get("seller", ""),
        "buyer": parties.get("buyer", ""),
        "consignee": parties.get("consignee", ""),
        "seller_address": seller_address,
        "buyer_address": buyer_address,
        "consignee_address": consignee_address,
        "payment": payment,
        "bank": bank_line,
        "columns": list(LOT_FIELDS),
        "stated": stated,
    }


def _stated_totals(documents):
    """Итог берётся, только если документы называют одно число. Два разных итога не выбираются."""
    merged = {}
    for doc in documents:
        for key, value in (doc.get("stated") or {}).items():
            if value is None:
                continue
            current = merged.get(key)
            if current is None and key not in merged:
                merged[key] = value
            elif current is None or abs(current - value) > 0.05:
                merged[key] = None
    return {key: value for key, value in merged.items() if value is not None}


def _has_measures(doc):
    for line in doc.get("lines") or []:
        if getattr(line, "freight", False):
            continue
        for name in ("pieces", "price", "amount", "packages", "net", "gross"):
            if getattr(line, name, None) is not None:
                return True
    return False


def _same_goods_sheet(doc, base_vendors):
    """Лист с другими артикулами не отдаёт в шапку свою валюту и стороны.
    Спецификация этой поставки остаётся: её артикул часто записан иначе, чем в инвойсе, а контракт лежит там."""
    if doc.get("role") == "specification" or "specification" in (doc.get("roles") or []):
        return True
    if not base_vendors:
        return True
    theirs = _vendor_set(doc.get("lines") or [])
    if not theirs:
        return True
    return bool(theirs & base_vendors)


def _pick(documents, role, prefer_pdf):
    found = [doc for doc in documents if _is_role(doc, role) and doc.get("lines")]
    if not found:
        return None
    measured = [doc for doc in found if _has_measures(doc)]
    pool = measured or found
    primary = [doc for doc in pool if doc.get("role") == role]
    pool = primary or pool
    if prefer_pdf:
        pdfs = [doc for doc in pool if doc.get("kind") == "pdf"]
        if pdfs:
            return pdfs[0]
    return max(pool, key=lambda doc: len(doc["lines"]))


def _is_role(doc, role):
    """Файл с двумя заголовками входит в обе роли. Строки при этом одни."""
    if doc.get("role") == role:
        return True
    return role in (doc.get("roles") or [])


def _companions(documents, invoice, specification, description, base, spec_lines):
    """Второй документ той же поставки дописывает пустые поля. Чужие артикулы лотами не становятся."""
    base_vendors = _vendor_set(base)
    used = {id(invoice), id(specification), id(description)}
    foreign = False
    for doc in documents:
        if id(doc) in used or doc.get("role") in {
            "packing", "duplicate", "draft", "gtd_form", "customs_appendix", "image", "scan",
            "description",
        }:
            continue
        lines = [line for line in doc.get("lines") or [] if not line.freight]
        theirs = _vendor_set(lines)
        if not theirs or not base_vendors:
            continue
        if theirs <= base_vendors:
            spec_lines.extend(lines)
            continue
        if not (theirs & base_vendors):
            foreign = True
    return foreign


def _vendor_set(lines):
    return {(line.vendor or "").strip().casefold() for line in lines or [] if (line.vendor or "").strip()}


def _header_anchors(documents):
    """Артикулы строк, которые уже разобраны. По ним чужой лист без строк в шапку не входит."""
    anchors = set()
    for doc in documents:
        if doc.get("role") == "duplicate":
            continue
        if doc.get("role") == "unknown" and not doc.get("lines"):
            continue
        for line in doc.get("lines") or []:
            vendor = (getattr(line, "vendor", None) or "").strip()
            if len(vendor) >= 4:
                anchors.add(vendor)
    return anchors


def _counts_for_header(doc, anchors):
    """Лист без строк и без артикула этой поставки в шапку, номер и валюту не входит.
    Расчёт (проходная, дорога) в шапку не входит."""
    if doc.get("role") in {"duplicate", "draft", "gtd_form", "customs_appendix"}:
        return False
    if doc.get("role") == "unknown" and not doc.get("lines"):
        if not anchors:
            return True
        text = doc.get("raw_text") or doc.get("text") or ""
        return any(anchor in text for anchor in anchors)
    return True


_PROFORMA_NO = re.compile(r"\bNO\.?\s*:?\s*([A-Z]{1,8}\d{3,}[A-Z0-9./\-]*)", re.I)


def _proforma_nos(documents):
    found = []
    for doc in documents:
        if doc.get("role") != "proforma":
            continue
        for match in _PROFORMA_NO.finditer(doc.get("text") or ""):
            token = match.group(1).strip(".:")
            if token not in found:
                found.append(token)
    return found


def _packing_lines(packings, weight_conflict, stated):
    """Без конфликта — все пакинги. При конфликте — только тот, чей итог мест совпал с CLL/TOTAL."""
    if not packings:
        return []
    if not weight_conflict:
        lines = []
        for doc in packings:
            lines.extend(doc["lines"])
        return lines
    target = (stated or {}).get("packages")
    if target is None:
        return []
    matched = []
    for doc in packings:
        goods = [line for line in doc["lines"] if not line.freight and not line.measure_group]
        total = sum(line.packages or 0 for line in goods)
        if abs(total - float(target)) <= 0.05:
            matched.append(doc)
    if len(matched) != 1:
        return []
    return list(matched[0]["lines"])


def _weights_differ(packings):
    """Один и тот же список строк с двумя итогами — конфликт. Части по машинам — не он.

    Итог мест тоже считается: PDF после восстановления слитых клеток даёт 684, а xlsx без
    середины клетки — 677 при тех же артикулах. Один брутто при разных местах всё равно конфликт.
    """
    totals = []
    package_totals = []
    piece_sets = []
    vendor_sets = []
    for doc in packings:
        goods = [line for line in doc["lines"] if not line.freight and not line.measure_group]
        totals.append(round(sum(line.gross or 0 for line in goods), 2))
        package_totals.append(round(sum(line.packages or 0 for line in goods), 2))
        # Строка без количества не должна ломать сортировку: None и число между собой не сравниваются.
        piece_sets.append(
            tuple(
                sorted(
                    (None if line.pieces is None else round(line.pieces, 3) for line in goods),
                    key=lambda value: (value is None, value or 0),
                )
            )
        )
        vendors = tuple(
            sorted(
                (
                    ((line.vendor or "").strip(), None if line.pieces is None else round(line.pieces, 3))
                    for line in goods
                    if (line.vendor or "").strip()
                ),
                key=lambda pair: (pair[0], pair[1] is None, pair[1] or 0),
            )
        )
        vendor_sets.append(vendors)
    same_weight = len(set(totals)) <= 1
    same_packages = len(set(package_totals)) <= 1
    if same_weight and same_packages:
        return False
    same_list = len(set(piece_sets)) == 1 and all(len(item) >= 2 for item in piece_sets)
    same_articles = all(vendor_sets) and len(set(vendor_sets)) == 1
    return same_list or same_articles


def _first(pattern, text):
    folded = collapse_letter_spacing(text)
    match = pattern.search(folded)
    if not match:
        match = pattern.search(folded.upper())
    if not match:
        return None
    token = match.group(1)
    token = re.split(r"(DATE|ISSUE|CONTRACT|FROM|TO)", token, maxsplit=1)[0]
    return token.strip(".:")


def _order_no(text):
    folded = collapse_letter_spacing(text or "")
    for match in _ORDER.finditer(folded):
        token = match.group(1)
        token = re.split(r"(DATE|ISSUE|CONTRACT|FROM|TO)", token, maxsplit=1)[0]
        token = token.strip(".:")
        if re.fullmatch(r"\d+(?:\.\d+)+", token):
            continue
        if any(ch.isdigit() for ch in token):
            return token
    return None


def _labeled_date(text):
    match = re.search(
        r"(?m)^\s*DATE\s*:\s*(\d{2}[./]\d{2}[./]\d{4}|[A-Za-z]{3,9}\.?\s*\d{1,2},\s*\d{4})",
        text or "",
        re.I,
    )
    if match:
        return " ".join(match.group(1).split())
    match = re.search(
        r"INVOICE\s*(?:NO|NR|NUMBER)?\.?(?:\s+AND\s+DATE)?\s*:?\s*[A-Z0-9][A-Z0-9./\-]*\s*[-–]\s*(\d{2}[./]\d{2}[./]\d{4})",
        text or "",
        re.I,
    )
    if match:
        return match.group(1)
    return _title_date(text)


def _spaced_date(text):
    """Подпись Date на своей строке, дата строкой ниже или после двоеточия. Год-месяц-день приводится к дд.мм.гггг."""
    lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
    pattern = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})|(\d{1,2})[./](\d{1,2})[./](\d{4})")
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^(?:invoice\s+)?date\s*:?\s*(.*)$", line)
        if not match:
            continue
        tail = match.group(1)
        if not tail and index + 1 < len(lines):
            tail = lines[index + 1]
        found = pattern.match(tail or "")
        if not found:
            named = _named_date(tail or "")
            if named:
                return named
            continue
        if found.group(1):
            year, month, day = found.group(1), found.group(2), found.group(3)
        else:
            day, month, year = found.group(4), found.group(5), found.group(6)
        if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
            continue
        return f"{int(day):02d}.{int(month):02d}.{year}"
    return ""


_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}


def _named_date(value):
    """Дата с названием месяца: `Mar. 31, 2014` или `31 March 2014`. На выходе дд.мм.гггг."""
    text = " ".join(str(value or "").split())
    match = re.match(r"(?i)([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if match:
        month, day, year = match.group(1), match.group(2), match.group(3)
    else:
        match = re.match(r"(?i)(\d{1,2})\s+([a-z]{3,9})\.?,?\s+(\d{4})", text)
        if not match:
            return ""
        day, month, year = match.group(1), match.group(2), match.group(3)
    number = _MONTHS.get(month.lower())
    if not number or not 1 <= int(day) <= 31:
        return ""
    return f"{int(day):02d}.{number:02d}.{year}"


def _letterhead_address(text, seller):
    """Адрес на строке сразу под именем продавца в шапке бланка, если подписи Address нет."""
    if not seller:
        return ""
    key = " ".join(seller.split()).casefold().split(",")[0].strip()
    lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if line.casefold().split(",")[0].strip() != key:
            continue
        nxt = lines[index + 1]
        if re.match(r"(?i)^(phone|tel|fax|e-?mail|web|address|exporter|seller)\b", nxt):
            continue
        if re.search(r"\d", nxt) and "," in nxt:
            chunks = [nxt]
            for more in lines[index + 2 : index + 4]:
                if not chunks[-1].endswith(",") or re.match(r"(?i)^(phone|tel|fax|e-?mail|web)\b", more):
                    break
                chunks.append(more)
            return " ".join(chunks)
    return ""


_TRANSPORT_REF = re.compile(
    r"(?i)\b(?:B/?L|bill\s+of\s+lading|container|seal|vessel)\s*(?:No\.?|number|#)?\s*:?\s*(?=[A-Z0-9\-]*\d)[A-Z0-9][A-Z0-9\-]*"
)
_CONTACT_TAIL = re.compile(r"(?i)\s*\b(?:OGRN|ОГРН|E-?mail|Tel\.?|Phone|Fax)\b.*$")


def _clean_address(value):
    """Номер коносамента, контейнер и контакты в почтовый адрес не входят."""
    lines = []
    for line in str(value or "").splitlines():
        line = _TRANSPORT_REF.sub("", line)
        line = _CONTACT_TAIL.sub("", line)
        line = " ".join(line.split()).strip(" ,;")
        if line:
            lines.append(line)
    return "\n".join(lines)


def _complete_address(address, text):
    """Колонки склеились и адрес оборван — полная запись того же адреса ищется в строке, где колонок нет."""
    flat = " ".join(str(address or "").split())
    if len(flat) < 20:
        return address
    head = flat[:25]
    best = address
    pattern = re.compile(
        r"(?i)address\s*:\s*([^:]+?)(?=\s+(?:OGRN|ОГРН|E-?mail|Tel\.?|Bank|INN|KPP|BIC|SWIFT)\b|$)"
    )
    for line in str(text or "").splitlines():
        for match in pattern.finditer(line):
            candidate = " ".join(match.group(1).split()).strip(" ,;")
            if candidate.startswith(head) and len(candidate) > len(" ".join(str(best).split())):
                best = candidate
    return best


def _drop_foreign_country(address, other):
    """Название страны соседней колонки (адрес продавца кончается PAKISTAN) посреди адреса покупателя не стоит."""
    words = re.findall(r"[A-Za-z]{4,}", str(other or ""))
    if not address or not words or not words[-1].isupper():
        return address
    # Своя страна идёт после запятой; слово без запятой перед ним — текст чужой колонки.
    return re.sub(rf"(?<=[A-Za-z.])\s+{words[-1]}\b", "", address).strip(" ,;")


def _weight_suspect(lot, freights=()):
    """Вес не похож на вес строки: нетто больше брутто, либо то же число стоит на сборах без товара."""
    net, gross = lot.get("net"), lot.get("gross")
    if net is not None and gross is not None and net > gross + 0.05:
        return True
    if net is None or lot.get("freight"):
        return False
    return any(
        fee.net is not None and abs(fee.net - net) < 0.005 for fee in freights
    )


def _title_date(text):
    """Дата на строке спецификации после «от». Подпись DATE и дата инвойса этим не затираются."""
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        window = " ".join(lines[max(0, index - 2) : index + 1])
        if not re.search(r"specification|спецификация", window, re.I):
            continue
        found = re.search(r"\bот\s+(\d{2}[./]\d{2}[./]\d{4})", line)
        if found:
            return found.group(1)
    return ""


def _contract_date(text):
    match = re.search(
        r"(?:CONTRACT|CONTRAT)\s*(?:NO|NR|NUMBER)?\.?\s*:?\s*\S+\s+dd\s+(\d{2}[./]\d{2}[./]\d{4})",
        text or "",
        re.I,
    )
    if match:
        return match.group(1)
    for line in str(text or "").splitlines():
        if not re.search(r"\b(?:contract|contrat|контракт)", line, re.I):
            continue
        found = re.search(
            r"(\d{2}[./]\d{2}[./]\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
            line,
        )
        if found:
            return " ".join(found.group(1).split())
    return ""


def _delivery(text):
    found = []
    for label in (
        r"terms of delivery(?:\s*/[^:\n]{0,40})?\s*:\s*([^\n]+)",
        r"delivery terms\s*:\s*([^\n]+)",
        r"inco\s*terms\s*:?\s*([^\n]+)",
    ):
        for match in re.finditer(label, text or "", re.I):
            value = " ".join(match.group(1).split())
            value = re.split(r"(?i)\b(?:shipment|payment|manufacturer|origin)\b", value)[0].strip(" .:")
            value = _trim_delivery_company(value)
            if value and not _same_term(value, found):
                found.append(value)
    for match in re.finditer(
        r"(?m)^\s*((?:EX[\s\-]*WORKS?|EXW|FOB|FCA|CIF|CFR|CPT|CIP|DAP|DDP|DPU)\b[^\n]{0,40})",
        text or "",
        re.I,
    ):
        value = " ".join(match.group(1).split())
        value = _trim_delivery_company(value)
        if value and not _same_term(value, found):
            found.append(value)
    if not found:
        return "", False
    if len(found) == 1:
        return found[0], False
    return " / ".join(found), True


def _trim_delivery_company(value):
    """Название фирмы в конце строки базиса — не условие поставки."""
    return re.sub(
        r"\s+[A-Z][A-Z0-9 .'/,&-]{6,}(?:LTD|LLC|INC|GMBH)\.?\s*$",
        "",
        str(value or ""),
    ).strip(" ,/")


def _term_code(value):
    head = re.split(r"[\s/.(]", str(value or "").strip(), maxsplit=1)[0].upper()
    compact = re.sub(r"[^A-Z]", "", head)
    if compact in {"EXW", "EXWORK", "EXWORKS"}:
        return "EXW"
    return compact or head


def _same_term(value, found):
    head = _term_code(value)
    for item in found:
        other = _term_code(item)
        if head == other or head in item.upper() or other in value.upper():
            return True
    return False


def _payment(text):
    """Срок оплаты. Два разных срока остаются оба, один не выбирается."""
    found = []
    pattern = r"(?:terms of payment|payment terms|условия оплаты|порядок оплаты)\s*:?\s*([^\n]+)"
    for match in re.finditer(pattern, text or "", re.I):
        value = " ".join(match.group(1).split())
        value = re.split(r"(?i)\b(?:bank|swift|seller|buyer)\b", value)[0].strip(" .:")
        if value and value not in found:
            found.append(value)
    if not found:
        return "", False
    if len(found) == 1:
        return found[0], False
    return " / ".join(found), True


def _bank_line(text):
    found = []
    for match in re.finditer(r"(?:^|\n)\s*(?:bank|банк)\s*:?\s*([^\n]+)", text or "", re.I):
        value = " ".join(match.group(1).split())
        if value and value not in found:
            found.append(value)
    if len(found) > 1:
        return " / ".join(found)
    return found[0] if found else ""


def _container(text):
    """Номер контейнера без пробела. «By truck» сюда не входит."""
    match = re.search(r"CONTAINER\s*:\s*([A-Z0-9]{4,})", text or "")
    return match.group(1) if match else ""


def _director(text):
    match = re.search(r"Director\s*:\s*(Mr\.?\s+[A-Za-z][A-Za-z .'\-]{2,40})", text or "")
    if not match:
        return ""
    return " ".join(match.group(1).split())


def _output_book_name(name):
    """Книга «… для ЭД» / «ТСД …» — выход профиля, не исходник поставщика.

    «Для ЭД» в начале длинного имени расчёта (спецификация Gaomi) — не выход, файл остаётся.
    """
    stem = Path(name).stem.strip()
    low = stem.casefold()
    if re.search(r"для[_\s]?эд\s*$", low):
        return True
    if re.match(r"(?i)^тсд\b", stem):
        return True
    return False


def _address_blocks(text):
    """Адрес после подписи своей стороны. BUYER и RECIPIENT в одной строке — два адреса ниже в том же порядке.
    Без подписи первая клетка Address — продавец, вторая — покупатель. Address в середине строки шапки тоже берётся.
    Bank address — не почтовый адрес."""
    lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
    pending = []
    assigned = {"seller": "", "buyer": "", "consignee": ""}
    fallback = []
    index = 0
    while index < len(lines):
        line = lines[index]
        values = _line_addresses(line)
        dual = _dual_party_labels(line)
        side = _party_side(line)
        if dual and not values:
            pending.extend(dual)
            index += 1
            continue
        if side and not values and _BENEFICIARY_ADDRESS.match(line) is None:
            pending.append(side)
            index += 1
            continue
        if not values:
            match = _BENEFICIARY_ADDRESS.match(line)
            if match:
                value = _postal_address(match.group(1) or "")
                if value:
                    values = [value]
                    if not pending and not assigned["seller"]:
                        assigned["seller"] = value
        if not values and index + 1 < len(lines):
            nxt = lines[index + 1]
            if (
                nxt
                and _party_side(nxt) is None
                and not _line_addresses(nxt)
                and (_ADDRESS_VALUE.match(line) or _BENEFICIARY_ADDRESS.match(line))
            ):
                values = [_postal_address(nxt)]
                index += 1
        # Подпись и Address на одной строке: значение сразу к этой стороне.
        owners = list(dual) if dual else ([side] if side else [])
        if owners and values and not pending:
            for owner, value in zip(owners, values):
                if value and owner in assigned and not assigned[owner]:
                    assigned[owner] = value
                if value and value not in fallback:
                    fallback.append(value)
            for value in values[len(owners) :]:
                if value and value not in fallback:
                    fallback.append(value)
            index += 1
            continue
        for value in values:
            if not value:
                continue
            if value not in fallback:
                fallback.append(value)
            if pending:
                owner = pending.pop(0)
                if owner in assigned and not assigned[owner]:
                    assigned[owner] = value
        index += 1
    if assigned["seller"] or assigned["buyer"] or assigned["consignee"]:
        seller = _longer_address(assigned["seller"], fallback)
        buyer = _longer_address(assigned["buyer"], fallback)
        consignee = _longer_address(assigned["consignee"], fallback)
        if not seller:
            seller = next((item for item in fallback if item not in {buyer, consignee}), "")
        if not buyer:
            buyer = next((item for item in fallback if item not in {seller, consignee}), "")
        if not consignee:
            consignee = next((item for item in fallback if item not in {seller, buyer}), "")
        return seller, buyer, consignee
    return (
        fallback[0] if fallback else "",
        fallback[1] if len(fallback) > 1 else "",
        fallback[2] if len(fallback) > 2 else "",
    )


def _dual_party_labels(line):
    """THE BUYER: RECIPIENT: — две подписи, не одно имя."""
    text = str(line or "")
    if not re.search(r"(?i)\bbuyer\b", text):
        return []
    if not re.search(r"(?i)\b(?:recipient|consignee|получатель)\b", text):
        return []
    if re.search(r"\b(LLC|LTD|LIMITED|ООО|GMBH)\b", text, re.I) and not re.search(
        r"(?i)buyer\s*:?\s*recipient|покупатель\s*:?\s*получатель", text
    ):
        return []
    return ["buyer", "consignee"]


_ADDRESS_LABEL = re.compile(
    r"(?i)(?:address|адрес|adress)\b(?:\s+of\s+location\s+and\s+post\s+address)?\s*/?\s*[^:]{0,40}:\s*"
)


def _line_addresses(line):
    """Все Address: на строке, в том числе после имени фирмы и два подряд в двух колонках.
    Bank address и адрес банка почтовым адресом не являются."""
    text = str(line or "")
    found = []
    for match in _ADDRESS_LABEL.finditer(text):
        prefix = text[max(0, match.start() - 5) : match.start()].casefold()
        if prefix.endswith("bank ") or prefix.endswith("банк "):
            continue
        value = _postal_address(text[match.end() :])
        if value and value not in found:
            found.append(value)
    return found


def _postal_address(value):
    """Повтор Address и банк на той же строке в почтовый адрес не входят. Второй адрес сбоку — другая сторона."""
    text = re.sub(r"(?i)^(?:address|адрес|adress)\s*:\s*", "", " ".join(str(value or "").split()))
    text = re.split(
        r"(?i)\b(?:address|адрес|adress|bank|банк|inn|инн|ogrn|огрн|kpp|кпп|contract|контракт|invoice|swift|account|tel\.?|phone|fax|e-?mail)\b"
        r"|(?:\b(?:I\s*N\s*N|K\s*P\s*P)\s*:)",
        text,
        maxsplit=1,
    )[0]
    return text.strip(" ,;.")


def _longer_address(value, fallback):
    """Короткий обрывок той же строки уступает адресу, где дом и комната уже есть."""
    best = value or ""
    for item in fallback:
        if best and item.startswith(best) and len(item) > len(best):
            best = item
        elif item and best.startswith(item) and len(best) > len(item):
            continue
    return best


_ADDRESS_VALUE = re.compile(
    r"(?i)^(?:address|адрес|adress)\b(?:\s+of\s+location\s+and\s+post\s+address)?\s*/?\s*[^:]{0,40}:\s*(.*)$"
)
_BENEFICIARY_ADDRESS = re.compile(
    r"(?i)^(?:beneficiary['’`]?s?\s+address|адрес\s+бенефициара)\s*:?\s*(.*)$"
)


def _party_side(line):
    if re.match(r"(?i)^(seller|buyer)['’]s\b", line or ""):
        return None
    match = re.match(
        r"(?i)^(?:the\s+)?(buyer|покупатель|importer|seller|продавец|exporter|recipient|consignee|получатель|грузополучатель)\b",
        line or "",
    )
    if not match:
        return None
    if len(line) > 48 and ":" not in line[:40]:
        return None
    token = match.group(1).lower()
    if token in {"buyer", "покупатель", "importer"}:
        return "buyer"
    if token in {"recipient", "consignee", "получатель", "грузополучатель"}:
        return "consignee"
    return "seller"


def _to_company(text):
    """«TO: компания» в шапке — покупатель, если слова Buyer нет. Два разных имени не выбирать."""
    found = []
    for match in re.finditer(r"(?m)^\s*TO\s*:\s*(.+)$", text or "", re.I):
        value = " ".join(match.group(1).split()).strip(" .")
        if not re.search(r"\b(LLC|LTD|GMBH|INC|CO\.|COMPANY|ООО|АО|ЗАО)\b", value, re.I) and '"' not in value:
            continue
        if value not in found:
            found.append(value)
    if len(found) == 1:
        return found[0]
    return ""


def _bad_party_name(value):
    """Хвост юридического абзаца и «именуемый в дальнейшем» именем стороны не являются."""
    text = " ".join(str(value or "").split())
    if not text or len(text) < 3:
        return True
    if re.fullmatch(r"а\.?\s*а\.?", text, re.I):
        return True
    if re.search(r"(?i)ввезен\w*\s+на\s+территори|таможенного\s+оформления|не\s+позднее\s+\d+\s+календар", text):
        return True
    if re.fullmatch(r"_+", text) or set(text) <= {"_", "-", "—", "–", " "}:
        return True
    return bool(
        re.search(
            r"(?i)(?:hereinafter|именуем\w*|on the one hand|on the other hand|acting basing|"
            r"действующего на основании|дальнейшем\s+to\s+as|to as the\s+(?:seller|buyer)|"
            r"^а\.?\s*а\.?\s*,|articles of association|^в\s+лице\b)",
            text,
        )
    )


def _party_head(value):
    """Имя до юридического хвоста. «HUANAN … LIMITED The company … hereinafter» → HUANAN … LIMITED."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = re.sub(r"(?i)^(?:vendor|seller|продавец)\s*:\s*", "", text).strip()
    match = re.search(
        r"(?i)(?:\s+The company\b|\s*,\s*именуем\w*|\s*,\s*hereinafter\b|\s*,\s*represented by\b|"
        r"\s*,\s*в лице\b|\s*,\s*acting bas(?:ed|ing)\b|\s*,\s*действующего на основании|"
        r"\s*,\s*on the one hand\b|\s+hereinafter\b|\s+именуем\w*|"
        r"\s+To\s*:|\s+Destination\s*:|\s+No\.\d|\s+для\s+таможен)",
        text,
    )
    if match and match.start() >= 3:
        text = text[: match.start()].strip(" ,.;")
    if _bad_party_name(text):
        return ""
    return text


def _vendor_company(text):
    """«Vendor: HAO NAI TE …» в шапке инвойса — продавец, если слова Seller нет."""
    match = re.search(r"(?m)^\s*Vendor\s*:\s*(.+)$", text or "", re.I)
    if not match:
        return ""
    value = " ".join(match.group(1).split()).strip(" .")
    value = re.split(r"(?i)\s+To\s*:|\s+Destination\s*:", value, maxsplit=1)[0].strip(" .")
    if not re.search(r"\b(LLC|LTD|LIMITED|GMBH|INC|CO\.|COMPANY|ООО)\b", value, re.I):
        return ""
    return value


def _packages_miss_stated(lots, stated):
    """Сумма мест строк не сходится с напечатанным итогом — пустые слитые клетки или пропуск.

    Расхождение на 1 при пустых строках блока часто шум итога (741 в TOTAL при 740 в строках).
    Явная дыра слитой клетки — это несколько мест, как 677 против 684.
    """
    target = (stated or {}).get("packages")
    if target is None:
        return False
    goods = [lot for lot in lots if not lot.get("freight")]
    if not goods:
        return False
    filled = [lot.get("packages") for lot in goods if lot.get("packages") not in (None, "")]
    empty = len(goods) - len(filled)
    total = sum(float(value) for value in filled) if filled else 0.0
    return bool(empty) and abs(total - float(target)) > 1.01


def _letterhead_seller(text):
    """Фирма над COMMERCIAL INVOICE, если слова Seller нет."""
    company = re.compile(r"\b(LTD|LIMITED|GMBH|LLC|INC|COMPANY)\b", re.I)
    title = re.compile(r"\b(COMMERCIAL\s+INVOICE|PACKING\s+LIST|SPECIFICATION|INVOICE)\b", re.I)
    picked = ""
    for line in str(text or "").splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        if title.search(line) and not re.search(r"\.(?:xls|xlsx|xlsm|pdf)\b", line, re.I):
            break
        if company.search(line) and not re.search(r"\bbuyer\b", line, re.I):
            picked = line
    return picked


def _party_continuation(text, name):
    """Строки сразу под именем покупателя, пока не началась следующая подпись."""
    if not name:
        return ""
    lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
    start = None
    for index, line in enumerate(lines):
        if name in line:
            start = index + 1
            break
    if start is None:
        return ""
    stop = re.compile(
        r"\b(contract|invoice|inv\.?\s*no|date\s*:|packing\s+list|commercial|specification|ex[\s\-]*works?|exw|fob|fca|ship\s*to|bill\s*to|inco\s*terms|sales\s*terms|payment)\b",
        re.I,
    )
    kept = []
    for line in lines[start:]:
        if stop.search(line) or re.match(r"^(seller|buyer|no\.?)\b", line, re.I):
            break
        glued = _glue_wrap(kept[-1], line) if kept else None
        if glued is not None:
            kept[-1] = glued
            continue
        if re.search(r"\d", line) and ("," in line or re.search(r"\b(ogrn|tin|inn|kpp)\b", line, re.I)):
            kept.append(line)
            continue
        if kept:
            break
    return "\n".join(kept)


def _glue_wrap(prev, line):
    """Перенос «Krasnogorsk c» + «ity» и последняя буква «RUSSI» + «A»."""
    if re.fullmatch(r"[A-Za-zА-Яа-яЁё]", line or ""):
        return prev + line
    last = prev.split()[-1] if prev.split() else ""
    match = re.match(r"([a-zа-яё]{1,6})(?=[,\s]|$)", line or "")
    if len(last) == 1 and last.isalpha() and match:
        return prev + match.group(1) + line[match.end() :]
    return None


def _ship_to_address(text):
    """Куда везут: строки с индексом под Ship To. Левая колонка (Inco Terms) — не адрес."""
    lines = [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]
    label = re.compile(r"(?i)\bship\s*to\b\s*:?\s*(.*)$")
    skip = re.compile(r"(?i)^(bill\s*to|ship\s*to|seller|buyer|inco|sales|payment|contract|invoice|commercial)\b")
    for index, line in enumerate(lines):
        match = label.search(line)
        if not match:
            continue
        chunks = []
        tail = match.group(1).strip(" :.")
        if re.search(r"\d{4,}", tail):
            chunks.append(tail)
        for nxt in lines[index + 1 : index + 12]:
            if skip.search(nxt):
                continue
            glued = _glue_wrap(chunks[-1], nxt) if chunks else None
            if glued is not None:
                chunks[-1] = glued
                continue
            if not chunks and (re.search(r"\d{4,}", nxt) or ("," in nxt and re.search(r"\d", nxt))):
                chunks.append(nxt)
                continue
            if chunks and re.match(r"^[a-zа-яё]", nxt or ""):
                chunks[-1] = f"{chunks[-1]} {nxt}"
                continue
            if chunks:
                break
        if chunks:
            return " ".join(chunks)
    return ""


def _contract(text):
    folded = (text or "").replace("\uff1a", ":")
    for found in _CONTRACT.finditer(folded):
        token = found.group(1).strip(" .")
        token = re.split(r"(?i)(?<=\d)(?:dated|date)", token, maxsplit=1)[0].strip(".:")
        token = re.split(r"(DATE|ISSUE|CONTRACT|FROM|TO)", token, maxsplit=1)[0].strip(".:")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{4}", token):
            continue
        if token.lower() in _CONTRACT_SKIP:
            continue
        if any(ch.isdigit() for ch in token) or "-" in token:
            return token
    for found in re.finditer(
        r"контракт\w*\s*(?:№|N[oо])\.?\s*([A-Za-z0-9][A-Za-z0-9./\-]*)",
        folded,
        re.I,
    ):
        return found.group(1)
    # Номер после двуязычной подписи: слово contract, хвост до двоеточия, затем номер.
    for found in re.finditer(
        r"(?:contract|контракт\w*)(?:\s*/\s*[^:\n]{0,80})?\s*:\s*([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9./\-]+)",
        folded,
        re.I,
    ):
        token = found.group(1).strip(" .")
        token = re.split(r"(DATE|ISSUE|CONTRACT|FROM|TO)", token, maxsplit=1)[0].strip(".:")
        if token.lower() in _CONTRACT_SKIP:
            continue
        if any(ch.isdigit() for ch in token) or "-" in token:
            return token
    spaced = re.search(
        r"(?:CONTRACT|CONTRAT)\s*(?:NO|NR|NUMBER|#|№|N[°º])?\.?\s*:?\s*(\d{1,6})\s+([A-Z][A-Z0-9./\-]+)",
        folded,
        re.I,
    )
    if spaced:
        return f"{spaced.group(1)} {spaced.group(2)}"
    return None


def _of_origin(text):
    """«of Turkish Origin» и «ALL TURKISH ORIGIN» без подписи country of origin. Два разных слова не выбираем."""
    skip = {"goods", "country", "their", "from", "place", "date", "this", "that"}
    found = []
    for match in re.finditer(r"\b([A-Za-z]{4,})\s+origin\b", text or "", re.I):
        word = match.group(1)
        if word.lower() in skip:
            continue
        if word.casefold() not in {item.casefold() for item in found}:
            found.append(word)
    if len(found) == 1:
        return found[0]
    return ""


def _labeled_numbers(text, label):
    """Номера с подписи и со следующей короткой строки. Длинная строка товара сюда не входит."""
    found = []
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        if not label.search(line):
            continue
        blob = line
        tail = ""
        if index + 1 < len(lines) and len(lines[index + 1]) <= 80:
            tail = lines[index + 1]
        for number in _plain_numbers(blob):
            if number not in found and len(found) < 8:
                found.append(number)
        # Следующая строка может держать номер и дату. Индекс перед названием города — не номер.
        for match in re.finditer(r"\b\d{5,12}\b", tail):
            after = tail[match.end() :]
            before = tail[: match.start()]
            if re.match(r"\s*,\s*[A-Za-zА-Яа-яЁё]", after):
                continue
            if re.match(r"-\d", after) or re.search(r"\d-$", before):
                continue
            number = match.group()
            if number not in found and len(found) < 8:
                found.append(number)
    return found


def _plain_numbers(text):
    """Кусок номера через дефис отдельно не хранится. В клетке это один номер."""
    found = []
    for match in re.finditer(r"\b\d{5,12}\b", text):
        after = text[match.end() :]
        before = text[: match.start()]
        if re.match(r"-\d", after) or re.search(r"\d-$", before):
            continue
        found.append(match.group())
    return found


_LABEL_ECHO = {
    "country", "of", "origin", "страна", "происхождения", "происхождение",
    "manufacturer", "производитель", "произведено",
}


def _one_label(text, label):
    """Одна подпись на всю поставку. Два разных значения не выбираем."""
    found = _label_hits(text, label)
    if len(found) == 1:
        return found[0]
    return ""


def _label_hits(text, label):
    found = []
    source = text or ""
    for match in re.finditer(label, source, re.I):
        tail = _label_value(source, match.end())
        if tail and tail not in found:
            found.append(tail[:120])
    return found


def _label_value(text, end):
    """Хвост подписи на том же языке пропускаем. Короткое значение может быть строкой ниже."""
    rest = text[end:]
    lines = re.split(r"[\r\n]", rest)
    tail = re.sub(r"^[\s:./\-]+", "", lines[0]).strip(" .")
    if tail and not _echo(tail):
        return _strip_repeated_label(tail)
    if len(lines) < 2:
        return ""
    nxt = lines[1].strip(" .")
    if not nxt or len(nxt) > 80 or _echo(nxt) or ":" in nxt:
        return ""
    return nxt


_REPEATED_LABEL = re.compile(
    r"(?:country\s+of\s+origin|страна\s+происхождения(?:\s+товара)?)",
    re.I,
)


def _strip_repeated_label(tail):
    """«TURKEY / СТРАНА ПРОИСХОЖДЕНИЯ ТОВАРА: ТУРЦИЯ» — повтор подписи не страна."""
    match = _REPEATED_LABEL.search(tail)
    if not match:
        return tail
    left = tail[: match.start()].strip(" /.:")
    right = re.sub(r"^[\s:./\-]+", "", tail[match.end() :]).strip(" .")
    parts = [part for part in (left, right) if part]
    return " / ".join(parts) if parts else tail


def _echo(tail):
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", tail.lower())
    return bool(words) and all(word in _LABEL_ECHO for word in words)


def _plain(line):
    return {
        "description": line.description,
        "amount": line.amount,
        "freight": True,
    }


def _printed_currency(code, text):
    """Код валюты, как он напечатан. Юань в документе чаще всего RMB, и CNY в файле может не быть вовсе."""
    if code != "CNY":
        return code or ""
    blob = str(text or "")
    if re.search(r"\bCNY\b", blob, re.I):
        return "CNY"
    if re.search(r"\bRMB\b", blob, re.I):
        return "RMB"
    return "CNY"


def _spec_rows(specification):
    """Строки спецификации: по ним после вердикта сверяются места свёрнутых рулонов."""
    if specification is None:
        return []
    return [
        {"vendor": line.vendor, "model": line.model, "packages": line.packages, "pieces": line.pieces}
        for line in specification.get("lines") or []
        if not line.freight
    ]


_TABLE_ROWS_LIMIT = 400
_TABLE_TEXT_LIMIT = 4000


def _document_table(doc):
    """Что код прочитал в этом файле. Строки без склейки, как они стоят в документе."""
    lines = doc.get("lines") or []
    rows = []
    for line in lines[:_TABLE_ROWS_LIMIT]:
        rows.append(
            {
                "article": (line.vendor or line.model or "").strip(),
                "model": line.model,
                "description": line.description,
                "rolls": line.packages,
                "qty": line.pieces,
                "unit": line.unit,
                "price": line.price,
                "amount": line.amount,
                "net_weight": line.net,
                "gross_weight": line.gross,
                "area": line.area,
                "width": line.width,
                "volume": line.volume,
                "hs_code": line.hs,
                "customs_code": line.hs_alt,
                "brand": line.brand,
                "manufacturer": line.producer,
                "country": line.origin,
                "color": line.finish,
                "size": line.size,
                "package_type": line.package_type,
                "freight": bool(line.freight),
                "raw": dict(line.extra or {}),
            }
        )
    note = ""
    if doc.get("role") == "duplicate":
        note = f"Тот же файл, что {doc.get('duplicate_of')}. Строки не удваиваются."
    elif not lines and not doc.get("readable", True):
        note = "Код не прочитал этот файл (скан или картинка без текстового слоя). Его читает модель по фото."
    elif not lines:
        note = "Строк товара код в этом файле не нашёл."
    headers: list[str] = []
    seen = set()
    for row in rows:
        for title in (row.get("raw") or {}):
            if title in seen:
                continue
            seen.add(title)
            headers.append(title)
    return {
        "role": doc.get("role") or "",
        "rows": rows,
        "headers": headers,
        "total_rows": len(lines),
        "note": note,
        "text": "" if rows else str(doc.get("text") or "")[:_TABLE_TEXT_LIMIT],
    }
