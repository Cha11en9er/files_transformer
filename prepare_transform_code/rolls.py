"""Рулоны одного дизайна в таблице поставки идут одной строкой.

Пакинг расписывает дизайн на рулоны, по строке на каждый (в колонке мест везде 1). Спецификация и человеческий
эталон держат дизайн одной строкой: сколько рулонов, сколько метров, какой вес. Чтение и склейка рулоны не теряют,
а эта функция сводит их уже после вердикта. Список рулонов остаётся в черновике кода.
"""

import re

from prepare_transform_code.numbers import parse_number

_SUM_FIELDS = ("pieces", "packages", "amount", "net", "net_primary", "gross", "volume", "area", "gross_with_pallet")
_METERS = re.compile(r"^(?:m|mt|mtr|mtrs|meters?|metres?|м|метр\w*)\.?$", re.I)
_PRICE_TOLERANCE = 0.02


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_number(value)


def _roll_like(lot):
    if lot.get("freight") or lot.get("measure_group"):
        return False
    packages = _num(lot.get("packages"))
    if packages is None or abs(packages - 1) > 0.05:
        return False
    kind = str(lot.get("package_type") or "").casefold()
    if "roll" in kind or "рулон" in kind:
        return True
    return bool(_METERS.match(str(lot.get("unit") or "").strip()))


def _design(lot):
    """Имя дизайна: артикул, а без него начало описания до разделителя перевода."""
    text = str(lot.get("vendor") or "").strip()
    if not text:
        text = str(lot.get("description") or "").split("//")[0]
    return " ".join(text.split()).casefold()


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _price_key(lot):
    price = _num(lot.get("price"))
    return None if price is None else round(price / _PRICE_TOLERANCE)


def _group_key(lot):
    return (
        _design(lot),
        _price_key(lot),
        str(lot.get("finish") or "").casefold().strip(),
        _digits(lot.get("hs")),
        str(lot.get("unit") or "").casefold().strip(),
    )


def _split_by_model(members):
    """Модель, которая повторяется у нескольких рулонов, — подгруппа (цвет, партия). Номер каждого рулона подгруппой не является."""
    models = [str(lot.get("model") or "").strip().casefold() for lot in members]
    repeated = {name for name in models if name and models.count(name) > 1}
    if not repeated:
        return [members]
    groups = {}
    order = []
    for lot, name in zip(members, models):
        key = name if name in repeated else ("single", id(lot))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(lot)
    return [groups[key] for key in order]


def _sum_field(members, name):
    values = [_num(lot.get(name)) for lot in members]
    if any(value is None for value in values):
        return None
    return round(sum(values), 4)


def _same(members, name):
    values = {lot.get(name) for lot in members if lot.get(name) is not None}
    return values.pop() if len(values) == 1 else None


def _merged(members, spec_rows):
    first = dict(members[0])
    for name in _SUM_FIELDS:
        first[name] = _sum_field(members, name)
    packages = first.get("packages")
    if packages is not None:
        first["packages"] = int(packages) if float(packages).is_integer() else packages
    models = {str(lot.get("model") or "").strip() for lot in members}
    shared = models.pop() if len(models) == 1 else ""
    first["model"] = shared
    # Общая модель (цвет, партия) различает строки одного дизайна. В артикул она дописывается, как это делает спецификация.
    vendor = str(first.get("vendor") or "").strip()
    if shared and vendor and shared.casefold() not in vendor.casefold():
        first["vendor"] = f"{vendor} {shared}"
    for name in ("width", "gsm", "unit_net"):
        first[name] = _same(members, name)
    for name in ("hs", "hs_alt", "origin", "brand", "producer", "finish", "package_type", "description", "order_ref"):
        if not first.get(name):
            first[name] = next((lot[name] for lot in members if lot.get(name)), "")
    first["split_of"] = None
    first["rolls_folded"] = len(members)
    first["unit_conflict"] = any(lot.get("unit_conflict") for lot in members)
    first["hs_alt_shipper"] = any(lot.get("hs_alt_shipper") for lot in members)
    conflicts = {}
    for lot in members:
        conflicts.update(lot.get("conflicts") or {})
    if conflicts:
        first["conflicts"] = conflicts
    else:
        first.pop("conflicts", None)
    if any(lot.get("_confidence") == "low" for lot in members):
        first["_confidence"] = "low"
    for name in ("_changes", "_verdict", "_reason"):
        first.pop(name, None)
    first["packages_conflict"] = _packages_differ(first, spec_rows)
    return first


def _packages_differ(lot, spec_rows):
    """Рулоны сведены, и места теперь можно сверить со спецификацией. Нет строки спецификации — расхождения нет."""
    vendor = " ".join(str(lot.get("vendor") or "").split()).casefold()
    model = " ".join(str(lot.get("model") or "").split()).casefold()
    labels = {label for label in (vendor, f"{vendor} {model}".strip(), model) if label}
    matched = []
    for row in spec_rows or []:
        label = " ".join(str(row.get("vendor") or "").split()).casefold()
        model_label = " ".join(str(row.get("model") or "").split()).casefold()
        if (label in labels or (model_label and model_label in labels)) and row.get("packages") is not None:
            matched.append(row)
    packages = _num(lot.get("packages"))
    if not matched or packages is None:
        return False
    total = sum(_num(row["packages"]) or 0 for row in matched)
    return abs(total - packages) > 0.05


def fold_rolls(lots, spec_rows=None):
    """Возвращает (лоты, заметки). Лоты без рулонной укладки не меняются."""
    candidates = {}
    for index, lot in enumerate(lots):
        if _roll_like(lot):
            candidates.setdefault(_group_key(lot), []).append(index)
    folded = {}
    skip = set()
    for indexes in candidates.values():
        if len(indexes) < 2:
            continue
        members = [lots[index] for index in indexes]
        by_id = {id(lot): index for lot, index in zip(members, indexes)}
        for part in _split_by_model(members):
            if len(part) < 2:
                continue
            positions = sorted(by_id[id(lot)] for lot in part)
            folded[positions[0]] = _merged(part, spec_rows)
            skip.update(positions[1:])
    if not folded:
        return list(lots), []
    out = []
    for index, lot in enumerate(lots):
        if index in skip:
            continue
        out.append(folded.get(index, lot))
    rolls = sum(item["rolls_folded"] for item in folded.values())
    note = f"Рулоны сведены по дизайну: было {rolls} строк, стало {len(folded)}. Каждый рулон виден на вкладке файла."
    return out, [note]
