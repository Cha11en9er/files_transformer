from prepare_transform_code.lines import Line


def build_lots(base_lines, packing_lines):
    goods = [line for line in base_lines if not line.freight]
    freights = [line for line in base_lines if line.freight]
    packing_goods = [line for line in packing_lines if not line.freight]
    used = set()
    lots = []
    for line in goods:
        match_at = _best(line, packing_goods, used)
        match = packing_goods[match_at] if match_at is not None else None
        if match_at is not None:
            used.add(match_at)
        lots.append(_merge(line, match))
    return lots, freights


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
    if left_vendors and right_vendors and set(left_vendors) & set(right_vendors):
        score += 100
    left_models = {code for code in left.anchors() if code.startswith("model:")}
    right_models = {code for code in right.anchors() if code.startswith("model:")}
    if left_models and left_models & right_models:
        score += 80
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
    return score


def _desc(line):
    for code in line.anchors():
        if code.startswith("desc:"):
            return code[5:]
    return ""


def _merge(base, packing):
    lot = _public(base)
    if packing is None:
        return lot
    for name in ("vendor", "model", "hs", "volume"):
        if not lot.get(name) and getattr(packing, name):
            lot[name] = getattr(packing, name)
    if packing.measure_group is not None:
        lot["measure_group"] = packing.measure_group
        return lot
    for name in ("packages", "gross", "net", "volume"):
        if lot.get(name) is None and getattr(packing, name) is not None:
            lot[name] = getattr(packing, name)
    if lot.get("pieces") is None and packing.pieces is not None:
        lot["pieces"] = packing.pieces
    return lot


def _public(line: Line):
    lot = {
        "description": line.description,
        "model": line.model,
        "vendor": line.vendor.strip() if line.vendor else "",
        "hs": line.hs,
        "pieces": line.pieces,
        "packages": line.packages,
        "price": line.price,
        "amount": line.amount,
        "gross": line.gross,
        "net": line.net,
        "volume": line.volume,
        "freight": line.freight,
        "measure_group": line.measure_group,
    }
    if line.measure_group:
        lot["packages"] = lot["gross"] = lot["net"] = None
    return lot
