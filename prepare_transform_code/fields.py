"""Синонимы шапок. Новая поставка добавляет фразу сюда, не условие по имени файла."""

import re

from prepare_transform_code.numbers import collapse_letter_spacing

# Более длинные фразы проверяются раньше коротких.
COLUMNS = (
    ("packages", ("q-ty packages", "q/ty cases", "qty cases", "qty of packages", "number of packages", "quantity of places", "quantity of place", "количество упаковок", "box qty", "koli adedi", "кол-во упаковок", "кол-во коробок", "кол-во мест", "количество мест", "количество коробок", "quantity/package", "места", "ctns", "ctn", "коробка", "boxes", "box")),
    ("unit_net", ("вес ед", "нетт за", "n.w./ctn", "nw/ctn", "unit n.w", "unit nw", "unit net")),
    ("gross_with_pallet", ("with pallet", "брутто с", "с весом паллет", "с паллет")),
    ("qty", ("q-ty items", "q/ty pairs", "quantity(mts)", "quantity (mts)", "qty(m)", "net meters", "net metres", "net meter", "net metre", "net mt", "goods pcs", "mal adedi", "кол-во пар", "кол-во штук", "кол-во шт", "количество")),
    ("price", ("unit price", "price per", "price for pair", "price for unit", "eur unit", "цена за пару", "цена за ед", "цена за шт", "unitprice")),
    ("amount", ("gesamtpreis", "total price", "total amount", "total value", "eur item", "amount", "стоимость", "общая стоимость")),
    ("gross", ("w/o pallet", "without pallet", "без паллет", "gross weight", "gesamtgewicht", "total g. weight", "total kg", "gross kg", "итого вес брутто", "g.w", "gw", "брутто", "brutto", "brut")),
    ("net", ("net weight", "total n. weight", "total nett", "итого вес нетто", "net kg", "n.w", "nw", "нетто", "netto", "nett")),
    ("volume", ("measurement", "volume", "cbm", "м куб", "m3")),
    ("area", ("q-ty m2", "qty m2", "sqm", "sq.m", "mt2", "м2", "кв.м", "кв м", "кв. м", "m2")),
    ("vendor", ("item number", "item code", "item no", "quality code", "vendor code", "код изделия", "cat,#", "cat.#", "article", "artikel", "articulo", "articolo", "artigo", "артикул", "маркировка", "货号", "art nr", "art.")),
    ("finish", ("тип покрытия", "finish", "colour", "color", "farbe", "couleur", "colore", "renk", "kolor", "цвет")),
    ("model", ("model", "наименование модели", "модели", "style", "модель", "modell", "modele", "modelo")),
    ("size", ("размер", "pointure", "größe", "grosse", "talla", "taglia", "tamanho", "beden", "rozmiar", "storlek", "size")),
    ("order_ref", ("order number", "номер заказа", "bestellnummer", "n° de commande", "numero de pedido", "cust po", "customer po", "po no", "po number", "sipariş", "siparis")),
    ("hs", ("customs code", "customs tariff", "custom tariff", "shipper's custom", "product code", "h.s. code", "h.s.code", "hscode", "hs code", "zolltarif", "harmonized", "таможенный код", "код тн", "код товара", "тн вэд", "тнвэд", "海关")),
    ("origin", ("country of orig", "страна происхождения", "ursprung", "pays d'origine", "pais de origen", "paese di origine", "pais de origem", "kraj pochodzenia", "menşe", "mense", "原产")),
    ("brand", ("trademark", "brand", "marque", "marca", "товарный знак")),
    ("producer", ("equipment supplier", "поставщик оборудования", "manufacturer", "изготовитель", "producer", "производитель", "hersteller", "fabricant", "fabricante", "üretici", "uretici")),
    ("pallet", ("№№ паллет", "номера паллет", "pallet no", "pall nr", "pallet", "паллет")),
    ("package_type", ("вид упаковок", "kind of package", "вид упаковки", "упаковка")),
    ("description", ("description", "name of product", "product name", "name of goods", "design name", "specifications", "goods", "item/description", "contents", "contenido", "bezeichnung", "désignation", "designation", "descripcion", "descrizione", "descricao", "наименование", "описание", "品名")),
    ("qty", ("quantity", "qty", "q-ty", "menge", "cantidad", "quantité", "quantite", "quantità", "quantita", "quantidade", "miktar", "ilość", "ilosc", "aantal", "antal", "кол-во", "数量")),
    ("price", ("price", "preis", "prix", "precio", "prezzo", "preco", "fiyat", "cena", "prijs", "pris", "цена", "单价")),
    ("amount", ("gesamt", "montant", "importe", "importo", "tutar", "bedrag", "belopp", "сумма", "金额")),
    ("packages", ("packages", "package", "roll", "carton", "colis", "paket", "bulto")),
)

_PARTY = (
    ("seller", ("seller", "exporter", "экспортер", "продавец", "поставщик", "verkäufer", "verkaeufer", "vendeur", "vendedor", "venditore", "fornecedor", "satici", "satıcı", "sprzedawca", "verkoper", "säljare", "saljare", "продавець", "卖方")),
    ("buyer", ("bill to", "sold to", "buyer", "byuer", "importer", "импортер", "покупатель", "käufer", "kaeufer", "acheteur", "comprador", "acquirente", "alıcı", "alici", "nabywca", "koper", "köpare", "kopare", "покупець", "买方")),
)

_ROLE_MARKS = (
    ("packing", ("PACKING AND WEIGHT", "PACKING LIST", "УПАКОВОЧН", "PACKLISTE", "LISTE DE COLISAGE", "LISTA DE EMBALAJE", "LISTA DI IMBALLAGGIO", "AMBALAJ LIST", "PAKLIJST", "PACKLISTA", "装箱单")),
    ("proforma", ("PRO FORMA", "PROFORMA")),
    ("invoice", ("COMMERCIAL INVOICE", "FAKTURA", "INVOICE", "СЧЕТ-ФАКТУРА", "СЧЁТ-ФАКТУРА", "ИНВОЙС", "RECHNUNG", "FACTURE", "FACTURA", "FATURA", "FATTURA", "发票")),
    ("specification", ("СПЕЦИФИКАЦИЯ", "СПЕЦИФИКАЦ", "SPECIFICATION", "SPEZIFIKATION", "ESPECIFICACION", "SPECYFIKACJA")),
)

def column_of(header):
    text = collapse_letter_spacing(" ".join(str(header or "").lower().replace("\n", " ").split()))
    text = " ".join(re.sub(r"\([^)]*\)", " ", text).split())
    text = _glue_broken_label(text)
    text = text.replace("weigth", "weight").replace("widht", "width")
    for dash in ("‐", "‑", "–", "—", "−"):
        text = text.replace(dash, "-")
    if not text:
        return None
    # G/M — граммы на метр ткани, не вес строки и не места.
    if text.replace(" ", "") in {"g/m", "гр/м", "g/mtr"}:
        return "gsm"
    # METERS — количество. M2 и MT2 — площадь, их это не забирает.
    if re.search(r"\bmeters?\b|\bmetres?\b", text) and not re.search(r"\bm2\b|\bmt2\b|кв", text):
        if not any(mark in text for mark in ("price", "цена", "weight", "вес", "width", "ширина")):
            return "qty"
    if re.search(r"\bwidth\b|\bширина\b", text) and not any(mark in text for mark in ("короб", "carton", "box", "report")):
        return "width"
    exact = {
        "net": "net",
        "n.w": "net",
        "nw": "net",
        "нетто": "net",
        "gross": "gross",
        "g.w": "gross",
        "gw": "gross",
        "брутто": "gross",
        "measure": "volume",
        "measurement": "volume",
    }
    if text in exact:
        return exact[text]
    # CBM/CTN — объём одной коробки. Итог строки — MEASUREMENT.
    if text.replace(" ", "") in {"measurement", "measure"} or "cbm" in text:
        if any(mark in text for mark in ("/ctn", "per ctn", "/carton", "per carton")):
            return None
        return "volume"
    for name, phrases in COLUMNS:
        if not any(phrase in text for phrase in phrases):
            continue
        # Длинная шапка описания начинается со слова «производитель», но это не колонка завода.
        if name == "producer" and any(word in text for word in ("наименование", "описание", "description")):
            continue
        if name == "finish" and any(word in text for word in ("наименование", "описание", "description", "goods")):
            continue
        # «Finishing batch» — номер партии, не покрытие. «finish» внутри «finishing» не берём.
        if name == "finish" and re.search(r"finish\w", text):
            continue
        # UNIT G.W. / UNIT N.W. и G.W./CTN — вес одной коробки, не вес строки.
        if name in {"gross", "net"} and re.search(r"(?<![a-z])unit(?![a-z])", text):
            continue
        if name == "gross" and any(mark in text for mark in ("/ctn", "per ctn", "/carton", "per carton", "короб")):
            continue
        if name == "net" and any(mark in text for mark in ("with box", "с короб")):
            continue
        if name == "net" and any(mark in text for mark in ("1 pair", "per pair", "per pc", "за шт", "за пар")):
            return "unit_net"
        if name == "net" and any(mark in text for mark in ("/ctn", "per ctn", "/carton", "per carton", "короб", "carton", "box")):
            return "unit_net"
        if name == "packages" and any(
            mark in text
            for mark in ("/ctn", "per ctn", "per carton", "n.w", "g.w", "weight", "вес", "нетто", "брут", "размер", "size", "cbm", "объ", "kg", "kgs", "кг")
        ):
            continue
        # ROLL No. — номер рулона, не количество мест.
        if name == "packages" and re.search(r"\broll\s*(?:no|nr|number|#|№)\b", text):
            continue
        # Qty/Ctn и «кол-во в коробке» — штуки в одной коробке, не количество строки.
        if name == "qty" and any(mark in text for mark in ("/ctn", "per ctn", "/carton", "в коробке", "per carton")):
            continue
        # «Количество рулонов» содержит «количество», но это места. Метры остаются количеством.
        if name == "qty" and re.search(r"roll|рулон", text) and not re.search(r"meter|metre|метр|m2|кв", text):
            return "packages"
        # «Размер коробки» — габарит упаковки, не размер товара.
        if name == "size" and any(mark in text for mark in ("короб", "carton", "ctn", "box", "report")):
            continue
        # TOTAL NETT содержит «total kg», но это нетто. Брутто — TOTAL KG без nett.
        if name == "gross" and any(mark in text for mark in ("nett", "netto", "нетто")):
            continue
        # «with primary packaging» — второе нетто строки, не замена первого и не вес коробки.
        if name == "net" and any(mark in text for mark in ("primary", "первич")):
            return "net_primary"
        return name
    if text == "item":
        return "vendor"
    if text == "design":
        return "model"
    if text in {"customer name", "customer"}:
        return "vendor"
    if text.replace(" ", "") in {"po#", "po", "p.o", "p.o."}:
        return "order_ref"
    if text == "packing":
        return "package_type"
    if re.fullmatch(r"total\s+(?:usd|eur|cny|gbp|pln|sek|rub|try|chf|jpy|rmb|yuan)", text):
        return "amount"
    # Колонка CODE / KOD — артикул. HS CODE и таможенный код сюда не входят.
    # Длинная клетка с цифрами — описание, куда код вписан, а не шапка артикула.
    if re.search(r"(?<![a-zа-яё])(code|kod|код)(?![a-zа-яё])", text):
        # Заводской код рядом с quality code. Артикулом его не делаем.
        if any(mark in text for mark in ("design code", "mill code")):
            return None
        if not any(mark in text for mark in ("hs", "нs", "customs", "custom", "tariff", "product", "тн", "товар", "таможен", "barcode", "баркод")):
            if len(text.split()) <= 3 and not re.search(r"\d", text):
                return "vendor"
    # «Марка» целиком. «Маркировка» — это артикул, не бренд.
    if re.search(r"(?<![а-яё])марка(?![а-яё])", text):
        return "brand"
    if text in {"тм", "tm"}:
        return "brand"
    if text.strip() in {"unit", "единица"} or any(token in text for token in ("ед. изм", "ед.изм", "изм", "единица", "birim")):
        if not any(bad in text for bad in ("price", "quantity", "qty", "eur", "кол-во", "цена", "вес")):
            return "unit"
    # «UNIT MT/PIECE» — колонка единицы. «UNIT PRICE» и «UNIT N.W.» сюда не входят.
    if re.match(r"^unit\b", text) and not any(
        bad in text for bad in ("price", "quantity", "qty", "weight", "вес", "n.w", "g.w", "net", "gross", "eur", "цена")
    ):
        return "unit"
    # «pcs, pcg, set» — колонка единицы. Какая именно, написано в клетке, не в списке шапки.
    tokens = re.findall(r"[a-zа-яё]+", text)
    if len(tokens) >= 2 and all(token in _UNIT_WORDS for token in tokens):
        return "unit"
    return None


_UNIT_WORDS = {
    "pcs", "pc", "pcg", "psc", "set", "sets", "шт", "штук", "штука",
    "pairs", "pair", "пар", "kg", "kgs", "кг",
}


def _glue_broken_label(text):
    """«PACKAG E»: перенос оставил одну букву. Склеивать, только если выходит известная подпись."""
    parts = text.split()
    if len(parts) < 2 or len(parts[-1]) != 1 or not parts[-1].isalpha():
        return text
    joined = parts[-2] + parts[-1]
    if joined not in {"package", "packages", "carton", "cartons", "brand", "netto", "gross"}:
        return text
    return " ".join(parts[:-2] + [joined])


def is_row_index(header, value):
    """Колонка Item / No. с числом 1, 2, 3 — номер строки. Текст в такой колонке — артикул."""
    text = " ".join(str(header or "").lower().split())
    if text not in {"item", "no", "no.", "poz", "pos", "№"}:
        return False
    return bool(re.fullmatch(r"\d{1,4}", str(value or "").strip()))


def is_stop_label(value):
    text = str(value or "").lower().replace("/", " ")
    if "say total" in text or re.search(r"\bsub\s*total\b|\bgrand\s*total\b", text):
        return True
    token = re.split(r"[^a-zа-яё0-9]+", text, maxsplit=1)[0]
    return token in {
        "total", "итого", "всего", "gesamt", "suma", "toplam", "razem",
        "totale", "celkem", "insgesamt", "montant", "合计", "summe",
    }


def is_header_row(cells):
    """Повтор шапки в теле. Длинное описание со словом внутри («nonwoven») товаром остаётся."""
    labels = 0
    numbers = 0
    for cell in cells:
        text = " ".join(str(cell or "").replace("\n", " ").split())
        if not text:
            continue
        label = column_of(text) if len(text) <= 80 else None
        if label:
            labels += 1
            continue
        if re.search(r"\d", text):
            numbers += 1
    return labels >= 3 and numbers == 0


def _role_text(text):
    """«of the invoice» и «the invoice» внутри фразы — не заголовок. INVOICE NO этой чисткой не трогать."""
    return re.sub(
        r"\b(?:of\s+the|the|this|our|your|an)\s+invoice\b",
        " ",
        str(text or ""),
        flags=re.I,
    )


def roles_of(text):
    """Вторая роль только если два заголовка стоят в одной строке. Слово ниже по листу роль не добавляет."""
    found = []
    for line in _role_text(text).splitlines()[:15]:
        upper = line.upper()
        hit = []
        for role, marks in _ROLE_MARKS:
            if any(_has_mark(upper, mark) for mark in marks):
                hit.append(role)
        if len(hit) < 2:
            continue
        for role in hit:
            if role not in found:
                found.append(role)
    return found


def role_of(text):
    upper = _role_text(text).upper()
    for role, marks in _ROLE_MARKS:
        for mark in marks:
            if _has_mark(upper, mark):
                return role
    return "unknown"


def _dedupe_side_by_side(value):
    """«Ejendals LLC Ejendals LLC» — одно имя в двух соседних колонках."""
    parts = value.split()
    if len(parts) >= 2 and len(parts) % 2 == 0:
        half = len(parts) // 2
        if parts[:half] == parts[half:]:
            return " ".join(parts[:half])
    folded = [re.sub(r"[^0-9a-zа-яё]+", "", part.casefold()) for part in parts]
    folded = [part for part in folded if part]
    if len(folded) >= 2 and len(folded) % 2 == 0:
        half = len(folded) // 2
        if folded[:half] == folded[half:]:
            raw = [part for part in parts if re.sub(r"[^0-9a-zа-яё]+", "", part.casefold())]
            return " ".join(raw[:half])
    return value


def _legal_tail(line):
    """Вторая строка названия фирмы: без цифр, с формой LTD, STI, GMBH. Адрес с номером сюда не входит."""
    text = str(line or "").strip()
    if not text or re.search(r"\d", text) or _row_label(text):
        return ""
    if re.search(r"\b(ltd|sti|gmbh|llc|inc|limited|anonim|sanayi|ticaret|konfeksiyon)\b", text, re.I):
        return text
    return ""


def _trim_party(value):
    """Дата и адрес на той же строке, что имя, в имя не входят."""
    return re.split(
        r"\s+(?:date|address|адрес|contract|контракт|the\s+delivery\s+basis|delivery\s+basis|terms\s+of\s+delivery|базис\s+поставки|inn|kpp|ogrn|огрн|инн|кпп|bank|банк|swift|tel)\b\s*:?",
        value,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,")


def _cut_other_party(value, role):
    """На одной строке «Покупатель … Продавец …» — каждое имя до следующей подписи."""
    low = value.lower()
    cut = len(value)
    for other, words in _PARTY:
        if other == role:
            continue
        for word in words:
            found = re.search(rf"(?<!\w){re.escape(word)}(?!\w)", low, re.I)
            if found:
                cut = min(cut, found.start())
    return value[:cut].strip(" :.-")


def _word_hit(text, word):
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.I) is not None


def _has_mark(upper, mark):
    if " " in mark or len(mark) >= 8:
        return mark in upper
    return re.search(rf"(?<![A-ZА-ЯЁ]){re.escape(mark)}(?![A-ZА-ЯЁ])", upper) is not None


_ROLE_GLUE = re.compile(
    r"^(?:[\"«»'„“”\s,.:;]+)*(?:с одной стороны|с другой стороны|hereinafter|on the one hand|on the other hand)\b",
    re.I,
)


def _name_before_role(line, match):
    """«Компания X, именуемая в дальнейшем Продавец» — имя стоит до подписи."""
    prefix = line[: match.start()]
    parts = re.split(r"именуем\w*|hereinafter|referred to as", prefix, flags=re.I)
    body = parts[-2] if len(parts) > 1 else parts[0]
    body = re.split(r"\s+в лице\b|\s+represented by\b", body, maxsplit=1, flags=re.I)[0]
    chunks = re.split(r"с одной стороны|on the one hand", body, flags=re.I)
    body = chunks[-1]
    body = re.sub(r"^(?:\s*и\s+|\s*and\s+)", "", body, flags=re.I)
    body = re.sub(r"^(?:the company|компания|company)\s+", "", body, flags=re.I)
    return body.strip(" ,:;\"'«»")


def _document_title(text):
    """Соседняя колонка «Commercial Invoice No» на одной строке с Bill to — не имя."""
    return bool(
        re.match(
            r"(?:commercial\s+)?(?:invoice|packing\s+list|specification|proforma|pro\s*forma)\b",
            str(text or "").strip(),
            re.I,
        )
    )


def _prose_role(line, match):
    """«the Seller fails» внутри фразы — не подпись. «seller for the goods» именем не является."""
    tail = line[match.end() :]
    if re.match(r"^[\s:./]*for\s+the\s+goods\b", tail, re.I):
        return True
    prefix = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", line[: match.start()])
    if len(prefix) < 4:
        return False
    first = re.match(r"^[\s:./]*([A-Za-zА-Яа-яЁё]+)", line[match.end() :])
    return bool(first and first.group(1)[:1].islower())


def _next_party_value(lines, index, party):
    """Перевод той же подписи (Buyer, затем Покупатель) именем не является."""
    cursor = index + 1
    while cursor < len(lines) and _bare_role(lines[cursor]) == party:
        cursor += 1
    if cursor >= len(lines) or _bare_role(lines[cursor]):
        return ""
    return _cut_other_party(lines[cursor][:160], party).strip(" /")


def _row_label(line):
    """Подпись стороны. Опечатка CONSINGNEE — та же подпись получателя, не имя."""
    role = _bare_role(line)
    if role:
        return role
    head = re.split(r"[/:]", str(line or ""), maxsplit=1)[0]
    token = re.sub(r"[\s.]+", "", head.lower()).strip(":")
    if token in {"recipient", "consignee", "consingnee", "получатель", "грузополучатель"}:
        return "consignee"
    return None


def _bare_role(line):
    """Подпись стороны без имени. «Покупатель//Buyer» — перевод той же подписи, не имя."""
    low = line.lower()
    for role, words in _PARTY:
        match = next(
            (found_at for word in words if (found_at := re.search(rf"(?<!\w){re.escape(word)}(?!\w)", low, re.I))),
            None,
        )
        if not match:
            continue
        # «Seller's bank» — притяжательное, не подпись стороны.
        if re.match(r"^['’]s\b", line[match.end() :], re.I):
            continue
        tail = re.sub(r"^[\s:.\-]+", "", line[match.end() :])
        if re.fullmatch(r"(address|adress|addr|name|nr|no)", tail, re.I):
            tail = ""
        if tail and _ROLE_GLUE.match(tail):
            return None
        if tail and not any(_word_hit(tail.lower(), word) for word in words):
            return None
        return role
    return None


_ALIAS_ROLE = {"exporter", "экспортер", "importer", "импортер"}


def party_after(text):
    """Подпись продавца или покупателя и значение на этой строке или на следующей."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    found = {}
    # Сначала Seller и Buyer. Exporter и Importer дописывают сторону, только если своей подписи не было.
    _fill_parties(lines, found, alias=False)
    _fill_parties(lines, found, alias=True)
    return found


def _fill_parties(lines, found, alias):
    index = 0
    while index < len(lines):
        role = _row_label(lines[index])
        nxt = _row_label(lines[index + 1]) if role and index + 1 < len(lines) else None
        if role and nxt and nxt != role:
            group = []
            cursor = index
            while cursor < len(lines):
                bare = _row_label(lines[cursor])
                if not bare or bare in group:
                    break
                group.append(bare)
                cursor += 1
            if len(group) >= 2:
                for offset, party in enumerate(group):
                    if party not in {"seller", "buyer"}:
                        continue
                    value_at = cursor + offset
                    if party in found or value_at >= len(lines) or _row_label(lines[value_at]):
                        continue
                    value = _cut_other_party(lines[value_at][:160], party)
                    if value:
                        found[party] = _dedupe_side_by_side(_trim_party(value))
                index = cursor + len(group)
                continue
        line = lines[index]
        low = line.lower()
        for party, words in _PARTY:
            if party in found:
                continue
            words = [word for word in words if (word in _ALIAS_ROLE) == alias]
            match = next(
                (found_at for word in words if (found_at := re.search(rf"(?<!\w){re.escape(word)}(?!\w)", low, re.I))),
                None,
            )
            if not match:
                continue
            if re.match(r"^['’]s\b", line[match.end() :], re.I):
                continue
            if _prose_role(line, match):
                continue
            tail = re.sub(r"^[\s:.\-]+", "", line[match.end() :])
            if re.fullmatch(r"(address|adress|addr|name|nr|no)", tail, re.I):
                tail = ""
            # «BUYER: RECIPIENT:» — вторая подпись, не имя. Имя строкой ниже.
            tail = re.sub(
                r"^(?:recipient|consignee|получатель|грузополучатель|notify(?:\s+party)?)\b\s*:?\s*",
                "",
                tail,
                flags=re.I,
            ).strip(" :.-")
            value = ""
            if tail and _ROLE_GLUE.match(tail):
                value = _name_before_role(line, match)
            elif tail and not _document_title(tail) and not any(_word_hit(tail.lower(), word) for word in words):
                value = _cut_other_party(tail, party)
            if not value:
                value = _next_party_value(lines, index, party)
            extra = _legal_tail(lines[index + 1] if index + 1 < len(lines) else "")
            if value and extra and extra.casefold() not in value.casefold():
                value = f"{value} {extra}"
            if value:
                found[party] = _dedupe_side_by_side(_trim_party(value))
        index += 1
    return found


# Один и тот же набор на любую поставку. Пустая клетка остаётся в этом столбце.
# Курс и вторая валюта (PLN рядом с EUR) сюда столбцом не входят: их читаем, но не выносим.
LOT_FIELDS = (
    "description",
    "vendor",
    "model",
    "size",
    "hs",
    "hs_alt",
    "origin",
    "brand",
    "producer",
    "unit",
    "unit_alt",
    "finish",
    "pieces",
    "packages",
    "package_type",
    "pallet",
    "price",
    "amount",
    "net",
    "net_primary",
    "gross",
    "gross_with_pallet",
    "unit_net",
    "volume",
    "area",
    "width",
    "gsm",
    "order_ref",
)


def stamp_header(header):
    """Колонка «маркировка», где в каждой строке название фирмы. Это не модель."""
    text = collapse_letter_spacing(" ".join(str(header or "").lower().replace("\n", " ").split()))
    return text in {"маркировка", "marking", "mark"}


def is_shipper_code(header):
    """Таможенный код поставщика — второй код, даже если колонка левее ТН ВЭД."""
    text = " ".join(str(header or "").lower().replace("\n", " ").split())
    return "shipper" in text or "поставщика" in text or "отправител" in text


def is_freight(value):
    text = str(value or "").lower()
    if "freight" in text or "перевоз" in text or "shipping" in text or "consolidation" in text:
        return True
    return "packing fee" in text or "упаковка для" in text
