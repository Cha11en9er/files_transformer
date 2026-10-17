from pathlib import Path

from prepare_transform_code.shipment import analyze

r = analyze(Path("documents/5_pravka/!Weavers"))
goods = [lot for lot in r["lots"] if not lot.get("freight")]
missing = [lot for lot in goods if lot.get("gross") is None]
print("missing", len(missing))
for lot in missing:
    print(
        lot.get("vendor"),
        lot.get("pieces"),
        lot.get("net"),
        lot.get("packages"),
        "split",
        lot.get("split_of"),
        "pcf",
        lot.get("packages_conflict"),
        "mg",
        lot.get("measure_group"),
        "finish",
        lot.get("finish"),
    )
print("flags", r.get("flags"))
print("pack docs", [(d["name"], d["role"], d["line_count"]) for d in r["documents"] if "PL" in d["name"].upper() or d["role"] == "packing"])
