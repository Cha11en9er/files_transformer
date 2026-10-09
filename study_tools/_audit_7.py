"""Снимок черновика по всем поставкам documents/7_pravka. Пишет study_tools/out/audit_7.txt."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prepare_transform_code.shipment import analyze


def shipment_dirs(root: Path) -> list[Path]:
    """Папка поставки — та, где лежат pdf/xls, не родитель группы."""
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        files = [
            p
            for p in path.iterdir()
            if p.is_file()
            and not p.name.startswith("~$")
            and p.suffix.lower() in {".pdf", ".xls", ".xlsx", ".xlsm", ".jpg", ".jpeg", ".png"}
        ]
        if files:
            found.append(path)
    return found


def summarize(folder: Path) -> str:
    result = analyze(folder)
    goods = [lot for lot in result.get("lots") or [] if not lot.get("freight")]
    fees = [lot for lot in result.get("lots") or [] if lot.get("freight")]
    packages = [lot.get("packages") for lot in goods]
    nets = [lot.get("net") for lot in goods]
    grosses = [lot.get("gross") for lot in goods]
    unit_nets = [lot.get("unit_net") for lot in goods]
    hs = sum(1 for lot in goods if lot.get("hs"))
    finish = sum(1 for lot in goods if lot.get("finish"))
    producer = sum(1 for lot in goods if lot.get("producer"))
    empty_pkg = sum(1 for lot in goods if lot.get("packages") in (None, ""))
    empty_net = sum(1 for lot in goods if lot.get("net") in (None, ""))
    roles = sorted({doc.get("role") for doc in result.get("documents") or []})
    flags = result.get("flags") or []
    lines = [
        f"=== {folder.relative_to(ROOT).as_posix()} ===",
        f"roles {roles}",
        f"seller {result.get('seller')!r}",
        f"buyer {result.get('buyer')!r}",
        f"seller_address {result.get('seller_address')!r}",
        f"buyer_address {result.get('buyer_address')!r}",
        f"consignee_address {result.get('consignee_address')!r}",
        f"invoice {result.get('invoice_no')!r} contract {result.get('contract_no')!r}",
        f"currency {result.get('currency')!r} delivery {result.get('delivery')!r}",
        f"lots {len(goods)} freight {len(fees)} flags {flags}",
        f"filled hs/finish/producer {hs}/{finish}/{producer}",
        f"empty packages/net {empty_pkg}/{empty_net}",
        f"packages sample {packages[:8]}",
        f"net sample {nets[:6]}",
        f"gross sample {grosses[:6]}",
        f"unit_net sample {unit_nets[:6]}",
        f"qty sample {[lot.get('pieces') or lot.get('qty') for lot in goods[:6]]}",
        f"amount sample {[lot.get('amount') for lot in goods[:6]]}",
        f"vendor sample {[lot.get('vendor') for lot in goods[:6]]}",
        f"desc sample {[str(lot.get('description') or '')[:48] for lot in goods[:4]]}",
        "",
    ]
    return "\n".join(lines)


def main():
    root = ROOT / "documents" / "7_pravka"
    out = ROOT / "study_tools" / "out" / "audit_7.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    chunks = []
    for folder in shipment_dirs(root):
        try:
            chunks.append(summarize(folder))
        except Exception as exc:  # noqa: BLE001 — снимок дыр, не падать на одной
            chunks.append(f"=== {folder.relative_to(ROOT).as_posix()} ===\nERROR {type(exc).__name__}: {exc}\n")
    out.write_text("\n".join(chunks), encoding="utf-8")
    print(f"wrote {out} folders {len(chunks)}")


if __name__ == "__main__":
    main()
