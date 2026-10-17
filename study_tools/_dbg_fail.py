from pathlib import Path

from prepare_transform_code.shipment import analyze
from prepare_transform_code.tests.test_shipments import load

# Shipment14
r = load("Шаблон 14")
goods = [lot for lot in r["lots"] if not lot["freight"]]
print("14 unit", repr(goods[0].get("unit")), "vendor", goods[0].get("vendor"))

# Shipment17
r = load("Шаблон 17")
unknown = [d for d in r["documents"] if d["role"] == "unknown"]
print("17 roles", [(d["name"][:40], d["role"]) for d in r["documents"]])
print("17 unknown", len(unknown))

# Weavers
folder = Path("documents/я_тестирую")
# find weavers
hits = list(Path("documents").rglob("*"))
cands = [p for p in hits if p.is_dir() and "weaver" in p.name.casefold()]
print("weaver folders", cands[:5])
if not cands:
    # from test source
    import inspect
    import prepare_transform_code.tests.test_shipments as t

    src = inspect.getsource(t.LiveFabricTest.test_weavers_twenty_five_rolls)
    for line in src.splitlines():
        if "documents" in line or "Path" in line or "folder" in line:
            print(line.strip())
else:
    r = analyze(cands[0])
    goods = [lot for lot in r["lots"] if not lot["freight"]]
    missing = [lot for lot in goods if lot.get("gross") is None]
    print("weavers lots", len(goods), "missing gross", len(missing))
    if missing:
        print("sample", missing[0].get("vendor"), missing[0].get("net"), missing[0].get("gross"))
