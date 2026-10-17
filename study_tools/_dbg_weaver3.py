from pathlib import Path

from prepare_transform_code.exceldoc import read_excel
from prepare_transform_code.join import build_lots
from prepare_transform_code.pdfdoc import read_pdf
from prepare_transform_code.shipment import _packing_lines, _stated_totals, _weights_differ, _is_role

folder = Path("documents/5_pravka/!Weavers")
docs = []
for path in sorted(folder.iterdir()):
    if path.suffix.lower() == ".pdf":
        doc = read_pdf(path)
        doc["name"] = path.name
        docs.append(doc)
    elif path.suffix.lower() in {".xlsx", ".xls"}:
        for sheet in read_excel(path):
            sheet["name"] = f"{path.name}/{sheet['sheet']}"
            docs.append(sheet)

for doc in docs:
    print(doc["name"], doc.get("role"), len(doc.get("lines") or []))

invoice = next(doc for doc in docs if _is_role(doc, "invoice"))
specification = next((doc for doc in docs if _is_role(doc, "specification")), None)
packings = [doc for doc in docs if _is_role(doc, "packing") and doc["lines"]]
weight_conflict = len(packings) > 1 and _weights_differ(packings)
stated = _stated_totals(docs)
packing_lines = _packing_lines(packings, weight_conflict, stated)
spec_lines = list(specification["lines"]) if specification else []
print("spec lines", len(spec_lines), "pack lines", len(packing_lines))
lots, _ = build_lots(invoice["lines"], packing_lines, spec_lines)
missing = [lot for lot in lots if lot.get("gross") is None and not lot.get("freight")]
print("missing after analyze-like build", len(missing))
for lot in missing:
    print(lot.get("vendor"), lot.get("pieces"), lot.get("net"), lot.get("finish"), lot.get("packages_conflict"))

# which spec lines touch MOSSLE
for line in spec_lines:
    if "MOSSLE" in str(line.vendor or "") or "MOSSLE" in str(line.model or "") or "MOSSLE" in str(line.description or ""):
        print(
            "spec",
            repr(line.vendor),
            repr(line.model),
            line.pieces,
            line.net,
            line.gross,
            line.packages,
        )
