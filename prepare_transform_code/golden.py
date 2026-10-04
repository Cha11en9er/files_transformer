"""Эталонный прогон: что код читает из каждой нумерованной папки `documents_final`.

Снимок хранится в `prepare_transform_code/golden/snapshot.json`. Правка кода меняет снимок только осознанно:
сначала `python -m prepare_transform_code.golden` показывает расхождения со снимком, потом `--update` фиксирует новый.

    python -m prepare_transform_code.golden              # сравнить все поставки со снимком
    python -m prepare_transform_code.golden --only 13 19 # только эти
    python -m prepare_transform_code.golden --update     # записать текущее чтение как эталон
"""

import argparse
import json
import re
import sys
from pathlib import Path

from prepare_transform_code.shipment import analyze

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ROOT / "documents_final"
SNAPSHOT = Path(__file__).resolve().parent / "golden" / "snapshot.json"

_HEADER = (
    "seller",
    "buyer",
    "contract",
    "contract_date",
    "invoice_no",
    "invoice_date",
    "currency",
    "delivery",
    "seller_address",
    "buyer_address",
)
_SUMS = ("pieces", "packages", "net", "gross", "amount", "volume", "area")


def folders():
    found = []
    for path in sorted(DOCUMENTS.iterdir()):
        match = re.search(r"(\d+)\s*$", path.name)
        if path.is_dir() and match:
            found.append((match.group(1), path))
    return sorted(found, key=lambda item: int(item[0]))


def snapshot(folder):
    result = analyze(folder)
    goods = [lot for lot in result["lots"] if not lot.get("freight")]
    sums = {}
    for name in _SUMS:
        values = [lot.get(name) for lot in goods if lot.get(name) is not None]
        sums[name] = round(sum(values), 3) if values else None
    return {
        "lots": len(goods),
        "freights": round(sum(fee.get("amount") or 0 for fee in result.get("freights") or []), 2),
        "sums": sums,
        "header": {name: " ".join(str(result.get(name) or "").split()) for name in _HEADER},
        "flags": sorted(result.get("flags") or []),
        "documents": sorted(f"{doc['role']}:{doc['line_count']}" for doc in result["documents"]),
    }


def diff(old, new, prefix=""):
    lines = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if isinstance(before, dict) and isinstance(after, dict):
            lines.extend(diff(before, after, f"{prefix}{key}."))
        elif before != after:
            lines.append(f"  {prefix}{key}: было {before!r}, стало {after!r}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args(argv)
    stored = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    current = dict(stored) if args.update else {}
    changed = 0
    for number, folder in folders():
        if args.only and number not in args.only:
            continue
        try:
            now = snapshot(folder)
        except Exception as exc:  # noqa: BLE001 - отчёт по всем поставкам важнее одной ошибки
            print(f"Поставка {number}: ошибка чтения {exc!r}")
            changed += 1
            continue
        current[number] = now
        before = stored.get(number)
        if before is None:
            print(f"Поставка {number}: нет в снимке")
            changed += 0 if args.update else 1
            continue
        lines = diff(before, now)
        if lines:
            changed += 1
            print(f"Поставка {number}: расхождения со снимком")
            print("\n".join(lines))
        else:
            print(f"Поставка {number}: без изменений")
    if args.update:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Снимок записан: {SNAPSHOT}")
        return 0
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
