import shutil
import tempfile
from pathlib import Path

from prepare_transform_code.shipment import analyze

src = Path("documents/5_pravka/!Weavers")
names = (
    "WEAVERS INVOICE.pdf",
    "WEAVERS PLIST.pdf",
    "Specification для 17768 WEAVERS.pdf",
    "Specification для 17768 WEAVERS.xlsx",
)
work = Path(tempfile.mkdtemp())
try:
    for name in names:
        shutil.copy2(src / name, work / name)
    r = analyze(work)
    goods = [lot for lot in r["lots"] if not lot.get("freight")]
    missing = [lot for lot in goods if lot.get("gross") is None]
    print("lots", len(goods), "missing gross", len(missing))
    for lot in missing:
        print(lot.get("vendor"), lot.get("pieces"), lot.get("net"), lot.get("gross"), lot.get("packages_conflict"), lot.get("finish"))
    print("docs", [(d["name"], d["role"], d["line_count"]) for d in r["documents"]])
    # packages conflict lots
    pc = [lot for lot in goods if lot.get("packages_conflict")]
    print("pcf", len(pc))
    for lot in pc[:5]:
        print(" ", lot.get("vendor"), lot.get("pieces"), lot.get("packages"), lot.get("net"), lot.get("gross"))
finally:
    shutil.rmtree(work, ignore_errors=True)
