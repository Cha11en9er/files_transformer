from pathlib import Path

from prepare_transform_code.fields import column_of
from prepare_transform_code.shipment import analyze
from prepare_transform_code.tests.test_shipments import load

r = load("Шаблон 17")
for doc in r["documents"]:
    print(doc["name"], doc["role"], "lines", doc["line_count"])
unknown = [doc for doc in r["documents"] if doc["role"] == "unknown" and doc["line_count"]]
print("unknown with lines", unknown)

r = load("Шаблон 14")
goods = [lot for lot in r["lots"] if not lot["freight"]]
print("14 first unit", repr(goods[0].get("unit")))
tables = r.get("document_tables") or {}
for name, table in tables.items():
    print("TABLE", name)
    for i, row in enumerate((table or [])[:5]):
        cells = list(row or [])[:12]
        print(i, [str(c)[:28] if c else "" for c in cells])
        print("   cols", [column_of(str(c)) if c else None for c in cells])
    if goods[0].get("unit") == "":
        break

r = analyze(Path("documents/5_pravka/!Weavers"))
goods = [lot for lot in r["lots"] if not lot["freight"]]
for lot in goods:
    if lot.get("gross") is None:
        print("missing", lot.get("vendor"), "net", lot.get("net"), "unit_net", lot.get("unit_net"), "pkg", lot.get("packages"))
