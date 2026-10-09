import re
from decimal import Decimal, ROUND_HALF_UP

from prepare_transform_code.fields import LOT_FIELDS, is_factory_list
from prepare_transform_code.lines import (
    Line,
    _is_contents,
    _letters,
    _plain_score,
    _product_title,
    description_holds,
    part_key,
)


def build_lots(base_lines, packing_lines, spec_lines=None):
    goods = _collapse_price_bands([line for line in base_lines if not line.freight])
    freights = [line for line in base_lines if line.freight]
    packing_goods = [line for line in packing_lines if not line.freight]
    used = set()
    lots = []
    for line in goods:
        group = _take(line, packing_goods, used)
        if len(group) > 1:
            whole = line.packages
            design_article = not (line.vendor or "").strip()
            created = []
            for index in group:
                lot = _merge_split(
                    line,
                    packing_goods[index],
                    design_is_article=design_article,
                )
                created.append(lot)
                # Итог «Roll: N» и N строк пакинга: каждая строка — одно место.
                if whole is not None and abs(whole - len(group)) < 0.05 and lot.get("packages") is None:
                    lot["packages"] = 1
                    if line.package_type and not lot.get("package_type"):
                        lot["package_type"] = line.package_type
                lots.append(lot)
            if len(created) > 1 and line.amount is not None and all(item.get("amount") is not None for item in created):
                rounded = [round(item["amount"], 2) for item in created]
                gap = round(line.amount - sum(rounded), 2)
                if abs(gap) <= 0.05:
                    for item, amount in zip(created, rounded):
                        item["amount"] = amount
                    created[-1]["amount"] = round(rounded[-1] + gap, 2)
        elif group:
            lots.append(_merge(line, packing_goods[group[0]]))
        else:
            lots.append(_merge(line, None))
    if spec_lines:
        _overlay(lots, [line for line in spec_lines if not line.freight])
    _fill_part_measures(lots, packing_goods)
    _apply_roll_weights(lots, [line for line in (spec_lines or []) if not line.freight], packing_goods)
    return lots, freights


def _fill_part_measures(lots, packing_lines):
    """Несколько пакингов одного изделия. Места складываются, уже полный итог не затирается.

    Складывать только когда штуки частей сходятся со строкой. Иначе один ТН ВЭД на много
    разных лотов собирает чужие места в одну клетку.
    """
    for lot in lots:
        key = part_key(Line(model=lot.get("model") or "", hs=lot.get("hs") or "", vendor=lot.get("vendor") or ""))
        if not key:
            continue
        if sum(1 for item in lots if part_key(Line(model=item.get("model") or "", hs=item.get("hs") or "", vendor=item.get("vendor") or "")) == key) != 1:
            continue
        parts = [line for line in packing_lines if part_key(line) == key]
        if len(parts) < 2:
            continue
        lot_pieces = lot.get("pieces")
        part_pieces = [line.pieces for line in parts if line.pieces is not None]
        if lot_pieces is not None and part_pieces and abs(sum(part_pieces) - float(lot_pieces)) > 0.05:
            continue
        values = [line.packages for line in parts if line.packages]
        total = sum(values)
        current = lot.get("packages")
        if not total:
            continue
        if current is None or (
            any(abs(current - value) < 0.05 for value in values) and abs(current - total) > 0.05
        ):
            lot["packages"] = total
            lot["packages_conflict"] = False


def _take(line, others, used):
    """Один артикул на инвойсе, который в пакинге разрезан на строки, даёт несколько лотов, если штуки в сумме те же."""
    codes = _vendors(line)
    same = []
    if codes:
        for index, other in enumerate(others):
            if index in used:
                continue
            if codes & _vendors(other):
                same.append(index)
    if same and line.pieces is not None:
        exact = [
            index
            for index in same
            if others[index].pieces is not None and abs(others[index].pieces - line.pieces) < 0.05
        ]
        if len(exact) == 1:
            used.add(exact[0])
            return exact
        if len(exact) > 1:
            priced = [index for index in exact if _price_close(line.price, others[index].price)]
            chosen = priced[0] if priced else exact[0]
            used.add(chosen)
            return [chosen]
        group = _unique_subset(same, others, line.pieces, line.price)
        if group:
            used.update(group)
            return group
    family = _family_group(line, others, used)
    if family:
        return family
    family_at = _packing_family(line, others, used)
    if family_at is not None:
        return [family_at]
    found = _best(line, others, used)
    if found is None:
        return []
    used.add(found)
    return [found]


def _vendors(line):
    codes = {code for code in line.anchors() if not code.startswith(("model:", "desc:"))}
    if codes:
        return codes
    # Артикула нет, а в коде товара 8–10 цифр. Вместе со штуками это та же строка.
    # Когда артикул есть, код ТН ВЭД ключом не становится.
    digits = re.sub(r"\D", "", str(line.hs or ""))
    if re.fullmatch(r"\d{8,10}", digits):
        codes.add("hs:" + digits)
    return codes


def _amount_is_product(base):
    """Сумма строки равна цене на количество. Частям пишем ту же формулу, не новое число."""
    if base.price is None or base.pieces is None or base.amount is None or not base.pieces:
        return False
    expected = base.price * base.pieces
    return abs(expected - base.amount) <= max(0.05, abs(base.amount) * 0.002)


def _unique_subset(indexes, others, target, price):
    """Один набор строк пакинга, чьи количества сходятся со строкой инвойса. Два таких набора не выбираем."""
    if target is None or not indexes:
        return None
    if len(indexes) > 18:
        total = sum(others[index].pieces or 0 for index in indexes)
        close = all(_price_close(price, others[index].price) for index in indexes)
        if close and abs(total - target) <= 0.05:
            return list(indexes)
        return None
    found = []
    for mask in range(1, 1 << len(indexes)):
        group = [indexes[bit] for bit in range(len(indexes)) if mask & (1 << bit)]
        if any(not _price_close(price, others[index].price) for index in group):
            continue
        total = sum(others[index].pieces or 0 for index in group)
        if abs(total - target) <= 0.05:
            found.append(group)
            if len(found) > 1:
                return None
    return found[0] if found else None


def _family_group(line, others, used):
    """Инвойс без артикула. Пакинг режет его на варианты с тем же первым словом, метры в сумме те же."""
    if line.pieces is None or (line.vendor or "").strip():
        return []
    token = _lead_word(line.description)
    if len(token) < 4:
        return []
    group = []
    for index, other in enumerate(others):
        if index in used or other.pieces is None:
            continue
        if _lead_word(other.description) != token:
            continue
        group.append(index)
    if len(group) < 2:
        return []
    total = sum(others[index].pieces or 0 for index in group)
    if abs(total - line.pieces) > 0.05:
        return []
    if not all(_price_close(line.price, others[index].price) for index in group):
        return []
    used.update(group)
    return group


def _lead_word(text):
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(text or ""))
    return words[0].casefold() if words else ""


def _short_design(part):
    """Короткое имя с цифрой — артикул варианта. Длинный код строки уходит в модель."""
    words = str(part.description or "").split()
    vendor = str(part.vendor or "").strip()
    if not words or len(words) > 4 or not vendor:
        return False
    if not any(ch.isdigit() for ch in part.description or ""):
        return False
    return any(ch.isdigit() for ch in vendor)


def _price_close(left, right):
    if left is None or right is None:
        return True
    return abs(left - right) <= 0.02


def _by_article_prefix(lot, spec_lines, used):
    """Артикул спецификации стоит в начале описания инвойса, штуки те же."""
    desc = (lot.get("description") or "").casefold().strip()
    if len(desc) < 4:
        return None
    hits = []
    for index, spec in enumerate(spec_lines):
        if index in used:
            continue
        vendor = (spec.vendor or "").strip()
        if len(vendor) < 4 or not desc.startswith(vendor.casefold()):
            continue
        if lot.get("pieces") is not None and spec.pieces is not None and abs(lot["pieces"] - spec.pieces) > 0.05:
            continue
        hits.append(index)
    if len(hits) == 1:
        return hits[0]
    return None


def _best(line, others, used):
    best_at = None
    best_score = 0
    for index, other in enumerate(others):
        if index in used:
            continue
        score = _score(line, other)
        if score > best_score:
            best_score = score
            best_at = index
    if best_score < 60:
        return None
    return best_at


def _score(left, right):
    score = 0
    left_vendors = [code for code in left.anchors() if not code.startswith(("model:", "desc:"))]
    right_vendors = [code for code in right.anchors() if not code.startswith(("model:", "desc:"))]
    if left_vendors and right_vendors and _vendor_hit(left_vendors, right_vendors):
        score += 100
    left_models = {code for code in left.anchors() if code.startswith("model:")}
    right_models = {code for code in right.anchors() if code.startswith("model:")}
    if left_models and left_models & right_models:
        score += 80
    left_label = _article_label(left)
    right_label = _article_label(right)
    if left_label and left_label == right_label:
        score += 100
    left_desc = _desc(left)
    right_desc = _desc(right)
    if left_desc and right_desc:
        if left_desc == right_desc:
            score += 70
        elif left_desc.startswith(right_desc[:24]) or right_desc.startswith(left_desc[:24]):
            score += 55
    if left.pieces is not None and right.pieces is not None and abs(left.pieces - right.pieces) < 0.01:
        score += 40
    elif left.pieces is not None and right.pieces is not None:
        score -= 30
    if _color_hit(left.finish, right.finish):
        score += 25
    return score


def _color_hit(left, right):
    """Один стиль двух цветов — разные лоты. «BLK» и «black / чёрный» — один цвет."""
    a = " ".join(str(left or "").lower().split())
    b = " ".join(str(right or "").lower().split())
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    left_token = re.split(r"[\s/]+", a)[0]
    right_token = re.split(r"[\s/]+", b)[0]
    short, long = (left_token, right_token) if len(left_token) <= len(right_token) else (right_token, left_token)
    expanded = _COLOR_ABBR.get(short)
    if expanded and expanded in (b if short == left_token else a):
        return True
    return len(short) >= 3 and long.startswith(short)


_COLOR_ABBR = {
    "blk": "black",
    "wht": "white",
    "brn": "brown",
    "nvy": "navy",
    "gry": "gray",
    "grn": "green",
}


def _vendor_hit(left_vendors, right_vendors):
    if set(left_vendors) & set(right_vendors):
        return True
    left_sku = [code[4:] for code in left_vendors if code.startswith("sku:")]
    right_sku = [code[4:] for code in right_vendors if code.startswith("sku:")]
    for left in left_sku:
        for right in right_sku:
            if len(left) >= 6 and len(right) >= 6 and (left.startswith(right) or right.startswith(left)):
                return True
    # Excel хранит артикул числом и съедает нуль в начале. 0904173 и 904173 — один код.
    for left in left_vendors:
        for right in right_vendors:
            if left.isdigit() and right.isdigit() and left.lstrip("0") and left.lstrip("0") == right.lstrip("0"):
                return True
    return False


def _desc(line):
    for code in line.anchors():
        if code.startswith("desc:"):
            return code[5:]
    return ""


def _merge_split(base, part, design_is_article=False):
    """Строка пакинга — отдельный лот. Сумма и штуки берутся у части, не у общей строки инвойса."""
    lot = _public(part)
    if base.price is not None:
        lot["price"] = base.price
    if part.amount is not None:
        lot["amount"] = part.amount
    elif _amount_is_product(base) and part.pieces is not None:
        lot["amount"] = round(base.price * part.pieces, 4)
    lot["pieces"] = part.pieces
    lot["description"] = _richer(base.description, part.description)
    lot["vendor"] = (part.vendor or base.vendor or "").strip()
    if design_is_article and _short_design(part):
        lot["model"] = part.vendor.strip()
        lot["vendor"] = part.description.strip()
    for name in ("model", "hs", "origin", "brand", "producer", "finish", "package_type", "pallet"):
        value = getattr(base, name)
        if name == "producer" and is_factory_list(value):
            continue
        if not lot.get(name) and value:
            lot[name] = value
    lot["unit_net"] = _finer(part.unit_net, base.unit_net)
    _apply_unit(lot, base.unit, part.unit)
    lot["split_of"] = base.pieces
    return lot


def _merge(base, packing):
    lot = _public(base)
    if packing is None:
        _apply_unit(lot, base.unit, "")
        return lot
    if getattr(packing, "family_total", False):
        # Вес и места семейства общие на несколько дизайнов. На один дизайн их не вешать.
        for name in ("vendor", "hs", "origin", "brand", "producer", "finish", "package_type", "width", "gsm"):
            value = getattr(packing, name, None)
            if name == "producer" and is_factory_list(value):
                continue
            if not lot.get(name) and value:
                lot[name] = value
        _fill_family_name(lot, packing)
        _apply_unit(lot, base.unit, packing.unit)
        return lot
    for name in ("vendor", "model", "hs", "volume", "origin", "brand", "producer", "finish", "package_type", "pallet", "size", "width", "gsm"):
        value = getattr(packing, name)
        if name == "producer" and is_factory_list(value):
            continue
        if not lot.get(name) and value:
            lot[name] = value
    lot["description"] = _richer(lot.get("description") or "", packing.description)
    _fill_family_name(lot, packing)
    lot["unit_net"] = _finer(lot.get("unit_net"), packing.unit_net)
    if packing.measure_group is not None:
        lot["measure_group"] = packing.measure_group
        lot["packages"] = lot["gross"] = lot["net"] = lot["net_primary"] = lot["gross_with_pallet"] = None
        _apply_unit(lot, base.unit, packing.unit)
        return lot
    packing_continued = getattr(packing, "span_continued", None) or set()
    if (
        lot.get("packages") is not None
        and packing.packages is not None
        and abs(lot["packages"] - packing.packages) > 0.05
        and "packages" not in packing_continued
    ):
        lot["packages_conflict"] = True
    for name in ("packages", "gross", "net", "net_primary", "volume", "gross_with_pallet", "pallet_count", "pallet_weight"):
        if lot.get(name) is None and getattr(packing, name, None) is not None:
            lot[name] = getattr(packing, name)
            if name == "packages" and "packages" in packing_continued:
                lot["packages_continued"] = True
    for name in ("amount", "pieces"):
        if name in packing_continued:
            lot[f"{name}_continued"] = True
    if lot.get("pieces") is None and packing.pieces is not None:
        lot["pieces"] = packing.pieces
    _apply_unit(lot, base.unit, packing.unit)
    return lot


def _overlay(lots, spec_lines):
    """Спецификация дописывает код, страну, марку. Веса и деньги из инвойса и пакинга не подменяет."""
    used = set()
    for lot in lots:
        probe = Line(
            vendor=lot.get("vendor") or "",
            model=lot.get("model") or "",
            hs=lot.get("hs") or "",
            finish=lot.get("finish") or "",
            pieces=lot.get("pieces"),
            price=lot.get("price"),
            description=lot.get("description") or "",
        )
        found = _by_article_prefix(lot, spec_lines, used)
        if found is None:
            found = _best(probe, spec_lines, used)
        if found is None:
            found = _hs_only(probe, spec_lines, used)
        if found is None:
            found = _by_qty_desc(probe, spec_lines, used)
        if found is None:
            continue
        spec = spec_lines[found]
        same_name = _article_label(probe) and _article_label(probe) == _article_label(spec)
        if (
            not same_name
            and _vendors(probe)
            and _vendors(spec)
            and not _vendor_hit(list(_vendors(probe)), list(_vendors(spec)))
        ):
            continue
        if probe.pieces is not None and spec.pieces is not None and abs(probe.pieces - spec.pieces) > 0.05:
            continue
        used.add(found)
        _take_fields(lot, spec, check=spec.pieces is not None)
        if spec.pieces is None:
            for other in lots:
                if other is lot:
                    continue
                other_probe = Line(vendor=other.get("vendor") or "", model=other.get("model") or "", description=other.get("description") or "")
                if not _vendor_hit(list(_vendors(probe)), list(_vendors(other_probe))):
                    continue
                # Строка без своего количества относится к группе лотов: веса группы с весом лота не сверяются.
                _take_fields(other, spec, check=False)
        for index, extra in enumerate(spec_lines):
            if index in used or extra.freight:
                continue
            if not _same_goods(probe, extra):
                continue
            used.add(index)
            _take_fields(lot, extra)
    _overlay_sums(lots, spec_lines)


def _overlay_sums(lots, spec_lines):
    """Строка спецификации с общей цифрой дописывает текст каждой части, если метры частей сходятся."""
    for spec in spec_lines:
        if spec.freight or spec.pieces is None or not (spec.vendor or "").strip():
            continue
        same = []
        for lot in lots:
            probe = Line(vendor=lot.get("vendor") or "", model=lot.get("model") or "", description=lot.get("description") or "")
            if _vendor_hit(list(_vendors(Line(vendor=spec.vendor))), list(_vendors(probe))):
                same.append(lot)
        if not same:
            continue
        group = _lot_subset(same, spec.pieces)
        if not group:
            continue
        for lot in group:
            _take_fields(lot, spec, check=False)
            if lot.get("pieces") is not None and abs(lot["pieces"] - spec.pieces) > 0.05:
                for name in ("net", "gross", "packages", "amount", "area"):
                    if getattr(spec, name) is not None and lot.get(name) == getattr(spec, name):
                        lot[name] = None
            if spec.area and lot.get("area") is None and lot.get("pieces"):
                lot["area"] = round(spec.area * lot["pieces"] / spec.pieces, 2)


def _lot_subset(lots, target):
    if len(lots) > 18:
        total = sum(lot.get("pieces") or 0 for lot in lots)
        if abs(total - target) <= 0.05:
            return list(lots)
        return None
    found = []
    for mask in range(1, 1 << len(lots)):
        group = [lots[bit] for bit in range(len(lots)) if mask & (1 << bit)]
        total = sum(lot.get("pieces") or 0 for lot in group)
        if abs(total - target) <= 0.05:
            found.append(group)
            if len(found) > 1:
                return None
    return found[0] if found else None


def _by_qty_desc(line, others, used):
    """Код только на спецификации: та же штука и то же имя."""
    name = _letters(line.description)
    if line.pieces is None or not name:
        return None
    hits = []
    for index, other in enumerate(others):
        if index in used or other.freight:
            continue
        if not (other.hs or other.origin or other.producer or other.finish or other.brand):
            continue
        if other.pieces is not None and abs(line.pieces - other.pieces) > 0.05:
            continue
        other_name = _letters(other.description)
        if other_name and (name == other_name or name in other_name or other_name in name):
            hits.append(index)
    if len(hits) == 1:
        return hits[0]
    return None


def _hs_only(line, others, used):
    """Артикула нет. Код товара находит строку, только если такая строка одна."""
    hs = {code for code in _vendors(line) if code.startswith("hs:")}
    if not hs:
        return None
    hits = []
    for index, other in enumerate(others):
        if index in used or not (hs & _vendors(other)):
            continue
        if line.pieces is not None and other.pieces is not None and abs(line.pieces - other.pieces) > 0.05:
            continue
        if not _price_close(line.price, other.price):
            continue
        hits.append(index)
    if len(hits) == 1:
        return hits[0]
    return None


def _same_goods(probe, line):
    """Та же строка в другом документе. Другое количество — другой лот."""
    if not _vendors(probe) or not _vendors(line) or not _vendor_hit(list(_vendors(probe)), list(_vendors(line))):
        return False
    if probe.pieces is not None and line.pieces is not None and abs(probe.pieces - line.pieces) > 0.05:
        return False
    return True


# Допуск, в пределах которого два документа считаются называющими одно число.
_CONFLICT_TOLERANCE = {"net": 0.2, "gross": 0.2}


def _note_conflict(lot, spec):
    """Число уже стоит и спецификация называет другое. Первое остаётся, расхождение запоминается."""
    for name, absolute in _CONFLICT_TOLERANCE.items():
        mine = lot.get(name)
        other = getattr(spec, name, None)
        if mine is None or other is None:
            continue
        if abs(mine - other) <= max(absolute, abs(mine) * 0.001):
            continue
        lot.setdefault("conflicts", {})[name] = other


def _take_fields(lot, spec, check=True):
    """Пустые поля дописать. Число, которое уже стоит, не затирать. Тот же цвет пишется полнее."""
    if check:
        _note_conflict(lot, spec)
    for name in ("vendor", "hs", "origin", "brand", "producer", "finish", "package_type", "pallet", "model", "size", "order_ref"):
        spec_value = getattr(spec, name)
        if name == "finish" and lot.get(name) and spec_value:
            if _color_hit(lot[name], spec_value):
                lot[name] = spec_value if len(str(spec_value)) > len(str(lot[name])) else lot[name]
            else:
                lot[name] = _richer(lot[name], spec_value)
        elif name == "vendor" and spec_value and _prefer_article(spec_value, lot.get("vendor") or ""):
            lot[name] = spec_value.strip()
        elif not lot.get(name) and spec_value:
            if name == "producer" and is_factory_list(spec_value):
                continue
            lot[name] = spec_value
    if (
        spec.packages is not None
        and lot.get("packages") is not None
        and abs(spec.packages - lot["packages"]) > 0.05
    ):
        lot["packages_conflict"] = True
    if spec.hs and lot.get("hs") and _hs_key(spec.hs) != _hs_key(lot.get("hs")):
        lot["hs_alt"] = spec.hs
        lot["hs_alt_shipper"] = False
    elif spec.hs_alt and lot.get("hs") and _hs_key(spec.hs_alt) != _hs_key(lot.get("hs")):
        lot["hs_alt"] = spec.hs_alt
        lot["hs_alt_shipper"] = bool(getattr(spec, "hs_alt_shipper", False))
    if not lot.get("unit") and spec.unit and not re.fullmatch(r"\d+[.,]?\d*", str(spec.unit).strip()):
        lot["unit"] = spec.unit
    for name in ("price", "amount", "width"):
        if lot.get(name) is None and getattr(spec, name, None) is not None:
            lot[name] = getattr(spec, name)
    for name in ("packages", "net", "net_primary", "gross", "volume", "area"):
        if lot.get(name) is None and getattr(spec, name) is not None:
            lot[name] = getattr(spec, name)
    _replace_carton_as_row(lot, spec)
    lot["unit_net"] = _carton_weight(lot.get("unit_net"), spec.unit_net, lot.get("net"), lot.get("packages"))
    lot["description"] = _richer(lot.get("description") or "", spec.description)


def _replace_carton_as_row(lot, spec):
    """Вес одной коробки в net/gross, когда TOTAL есть на спецификации: net×места ≈ TOTAL.

    Места лота и спецификации должны совпадать: иначе итог семейства (22 рулона)
    случайно равен весу части × её 5 местам, и вес части затирается.
    """
    packages = lot.get("packages")
    if not packages or packages <= 1:
        return
    if spec.packages is not None and abs(float(spec.packages) - float(packages)) > 0.05:
        return
    for name in ("net", "gross"):
        current = lot.get(name)
        better = getattr(spec, name, None)
        if current is None or better is None:
            continue
        if current >= better * 0.5:
            continue
        if abs(current * float(packages) - float(better)) <= max(0.5, abs(better) * 0.02):
            if lot.get("unit_net") is None and name == "net":
                lot["unit_net"] = current
            lot[name] = better


def _hs_key(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _public(line: Line):
    lot = {name: getattr(line, name, None) for name in LOT_FIELDS}
    lot["vendor"] = line.vendor.strip() if line.vendor else ""
    lot["description"] = line.description or ""
    for name in ("model", "size", "hs", "hs_alt", "origin", "brand", "producer", "finish", "unit", "unit_alt", "package_type", "pallet", "order_ref"):
        if not lot.get(name):
            lot[name] = ""
    lot["freight"] = line.freight
    lot["measure_group"] = line.measure_group
    lot["hs_alt_shipper"] = bool(getattr(line, "hs_alt_shipper", False) and line.hs_alt)
    lot["pallet_count"] = getattr(line, "pallet_count", None)
    lot["pallet_weight"] = getattr(line, "pallet_weight", None)
    lot["unit_conflict"] = False
    lot["packages_conflict"] = False
    lot["split_of"] = None
    continued = getattr(line, "span_continued", None) or set()
    for name in ("packages", "amount", "pieces"):
        if name in continued:
            lot[f"{name}_continued"] = True
    if line.measure_group:
        lot["packages"] = lot["gross"] = lot["net"] = lot["net_primary"] = lot["gross_with_pallet"] = None
    return lot


def _interleaved(text):
    """Английский и русский склеены через букву. Такой слой не заменяет нормальное описание."""
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    if len(letters) < 40:
        return False
    switches = 0
    prev = None
    for ch in letters:
        script = "c" if "а" <= ch.lower() <= "я" or ch.lower() == "ё" else "l"
        if prev and script != prev:
            switches += 1
        prev = script
    return switches >= len(letters) * 0.35


def _richer(left, right):
    left = " ".join(str(left or "").split())
    right = " ".join(str(right or "").split())
    if _interleaved(right) and left:
        return left
    if _interleaved(left) and right:
        return right
    if _is_contents(right) and left:
        right = _product_title(right)
    if _is_contents(left) and right:
        left = _product_title(left)
    if left and right and _letters(left) == _letters(right):
        return left if _plain_score(left) >= _plain_score(right) else right
    if not left:
        return right
    if not right or description_holds(left, right):
        return left
    if description_holds(right, left):
        return right
    # Дописать только те куски справа, которых ещё нет. «/ /» и повтор языка не создавать.
    right_parts = [part.strip() for part in re.split(r"\s*/+\s*", right) if part.strip()]
    missing = [part for part in right_parts if not description_holds(left, part)]
    if not missing:
        return left
    if len(missing) == 1 and len(right_parts) > 1:
        return left + " // " + missing[0]
    if "/" in right and len(right_parts) == 2:
        head, tail = right_parts[0], right_parts[1]
        if head.casefold() in left.casefold() and tail.casefold() not in left.casefold():
            return left + " // " + tail
        if tail.casefold() in left.casefold() and head.casefold() not in left.casefold():
            return left + " // " + head
    return left + " // " + (" // ".join(missing) if missing != right_parts else right)


def _prefer_article(candidate, current):
    """Артикул с цифрами сильнее словесной метки вроде TABLE SLIDE из колонки ITEM."""
    left = str(candidate or "").strip()
    right = str(current or "").strip()
    if not left:
        return False
    if not right:
        return True
    left_digits = sum(ch.isdigit() for ch in left)
    right_digits = sum(ch.isdigit() for ch in right)
    if left_digits >= 2 and right_digits == 0:
        return True
    if left_digits > right_digits and re.search(r"[A-Za-z]\d|\d[A-Za-z]", left):
        return True
    return False


def _carton_weight(left, right, net, packages):
    """N.W./CTN с числом всей строки — это нетто партии, не вес коробки."""
    kept = []
    for value in (left, right):
        if value is None:
            continue
        if net is not None and packages and packages > 1 and value >= net * 0.9:
            continue
        kept.append(value)
    if not kept:
        return None
    if len(kept) == 1:
        return kept[0]
    return _finer(kept[0], kept[1])


def _finer(left, right):
    """0,05 и 0,0497 — одно число, напечатанное с разным числом знаков. Оставляем более точное."""
    if left is None:
        return right
    if right is None:
        return left
    if abs(left - right) < 1e-9:
        return left
    if _rounds_to(right, left):
        return right
    if _rounds_to(left, right):
        return left
    return left


def _rounds_to(precise, rounded):
    for digits in (2, 3, 4):
        if abs(round(precise, digits) - rounded) <= (10 ** (-digits)) / 2 + 1e-9:
            return abs(precise - rounded) > 1e-9
    return False


def _apply_unit(lot, left, right):
    left_kind = _unit_kind(left)
    right_kind = _unit_kind(right)
    if left_kind and right_kind and left_kind != right_kind:
        lot["unit_conflict"] = True
        lot["unit"] = right or left
        lot["unit_alt"] = left
        return
    lot["unit"] = right or left or lot.get("unit") or ""


def _unit_kind(text):
    raw = str(text or "").lower().replace(" ", "")
    if not raw:
        return ""
    if any(token in raw for token in ("set", "компл", "kit")):
        return "set"
    if any(token in raw for token in ("pair", "пар", "para")):
        return "pair"
    if any(token in raw for token in ("szt", "шт", "psc", "pcs", "pc")):
        return "piece"
    return raw


def _family_token(text):
    """«Noble 110» и «SOFA FABRIC Noble» — одно семейство. Голое слово семейством не считаем."""
    words = str(text or "").replace("\n", " ").split()
    if len(words) >= 2 and any(ch.isdigit() for ch in words[-1]):
        return words[0].casefold()
    if len(words) >= 2:
        return words[-1].casefold()
    return ""


def _name_words(text):
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(text or ""))


def _same_family(left, right):
    """«Velvet LUX 03» и «SOFA FABRIC Velvet LUX», «Lazy Silver» и «SOFA FABRIC Lazy» — одно семейство."""
    left_words = _name_words(left)
    right_words = _name_words(right)
    if not left_words or not right_words:
        return False
    return _tail_opens(left_words, right_words) or _tail_opens(right_words, left_words)


def _tail_opens(summary, design):
    head = [word.casefold() for word in design]
    tail = [word.casefold() for word in summary]
    if len(head) >= 2 and head[-1].isdigit():
        head = head[:-1]
    if not head or not tail:
        return False
    for size in range(min(len(tail), len(head)), 0, -1):
        if tail[-size:] != head[:size]:
            continue
        if size == 1 and tail[-1] in {"fabric", "sofa", "textile"}:
            continue
        return True
    return False


def _article_label(line):
    """Одно и то же имя дизайна: на инвойсе в модели, в спецификации в артикуле."""
    for value in (line.vendor, line.model):
        text = " ".join(str(value or "").split()).casefold()
        if text:
            return text
    return ""


def _collapse_price_bands(lines):
    """Строка без своего номера, которая суммирует дизайны над ней и несёт цену, — не лот."""
    kept = []
    band = []
    for line in lines:
        if _is_price_band(line, band):
            _apply_band_price(line, band)
            band = []
            continue
        band.append(line)
        kept.append(line)
    return kept


def _is_price_band(line, band):
    if line.price is None or not band:
        return False
    name = line.model or line.description
    if not _name_words(name):
        return False
    if any(item.price is not None for item in band):
        return False
    if any(not _same_family(name, item.model or item.description) for item in band):
        return False
    packs = [item.packages for item in band]
    pieces = [item.pieces for item in band]
    pack_ok = line.packages is not None and all(value is not None for value in packs) and abs(sum(packs) - line.packages) <= 0.05
    piece_ok = line.pieces is not None and all(value is not None for value in pieces) and abs(sum(pieces) - line.pieces) <= 0.05
    if line.packages is not None and line.pieces is not None:
        return pack_ok and piece_ok
    return pack_ok or piece_ok


def _apply_band_price(total, band):
    for item in band:
        if item.price is None:
            item.price = total.price
    if total.amount is None or not total.pieces or total.price is None:
        return
    if abs(total.amount - total.price * total.pieces) > max(0.05, abs(total.amount) * 0.002):
        return
    amounts = []
    for item in band:
        if item.pieces is None:
            return
        amounts.append(_money(item.price * item.pieces))
    gap = _money(total.amount - sum(amounts))
    if abs(gap) > 0.05:
        return
    for item, amount in zip(band, amounts):
        if item.amount is None:
            item.amount = amount
    if gap and band[-1].amount is not None:
        band[-1].amount = _money(band[-1].amount + gap)


def _money(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _packing_family(line, others, used):
    """Пакинг семейства клеится к каждому дизайну. Общий вес остаётся на строке семейства."""
    name = line.model or line.description
    if not _name_words(name):
        return None
    hits = [
        index
        for index, other in enumerate(others)
        if _same_family(name, other.model or other.description)
    ]
    if len(hits) != 1:
        return None
    index = hits[0]
    other = others[index]
    shared = (
        line.packages is not None
        and other.packages is not None
        and abs(line.packages - other.packages) > 0.05
    )
    if shared:
        other.family_total = True
        return index
    if index in used:
        return None
    used.add(index)
    return index


def _fill_family_name(lot, packing):
    if lot.get("description"):
        return
    family_name = " ".join(str(packing.model or packing.description or "").split())
    if not family_name or family_name == lot.get("model"):
        return
    if not _same_family(family_name, lot.get("model") or ""):
        return
    lot["description"] = family_name


def _design_key(text):
    head = str(text or "").split("//")[0]
    return " ".join(head.split()).casefold()


def _apply_roll_weights(lots, spec_lines, packing_lines):
    """Рулоны одного дизайна — его детализация. Вес берём, если сумма сходится с пакингом семейства."""
    grouped = {}
    for spec in spec_lines:
        key = _design_key(spec.model or spec.description)
        if not key:
            continue
        grouped.setdefault(key, []).append(spec)
    pending = {}
    for lot in lots:
        key = _design_key(lot.get("model") or lot.get("description"))
        rows = grouped.get(key) or []
        if not rows:
            continue
        meters = sum(row.pieces or 0 for row in rows)
        if lot.get("pieces") is None or abs(meters - lot["pieces"]) > 0.05:
            continue
        if lot.get("packages") is not None and abs(len(rows) - lot["packages"]) > 0.05:
            continue
        pending.setdefault(_family_token(lot.get("model") or ""), []).append((lot, rows))
    for token, group in pending.items():
        nets = [_money(sum(row.net or 0 for row in rows)) for _lot, rows in group]
        grosses = [_money(sum(row.gross or 0 for row in rows)) for _lot, rows in group]
        packing = _one_family_packing(token, packing_lines)
        if packing is not None and packing.net is not None and abs(sum(nets) - packing.net) > 0.05:
            continue
        if packing is not None and packing.gross is not None and abs(sum(grosses) - packing.gross) > 0.02:
            continue
        for (lot, rows), net, gross in zip(group, nets, grosses):
            if lot.get("net") is None and any(row.net is not None for row in rows):
                lot["net"] = net
            if lot.get("gross") is None and any(row.gross is not None for row in rows):
                lot["gross"] = gross
            widths = {row.width for row in rows if row.width is not None}
            if lot.get("width") is None and len(widths) == 1:
                lot["width"] = widths.pop()


def _one_family_packing(token, packing_lines):
    if not token:
        return None
    hits = [line for line in packing_lines if _family_token(line.model or line.description) == token]
    if len(hits) == 1:
        return hits[0]
    return None

