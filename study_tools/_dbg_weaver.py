from pathlib import Path

from prepare_transform_code.join import build_lots
from prepare_transform_code.pdfdoc import read_pdf
from prepare_transform_code.exceldoc import read_excel

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

inv = next(doc for doc in docs if doc.get("role") == "invoice")
pack = next(doc for doc in docs if doc.get("role") == "packing")
spec = next((doc for doc in docs if doc.get("role") == "specification"), None)

print("packing MOSSLE 100 measure_group:")
for line in pack["lines"]:
    if line.vendor == "MOSSLE 100":
        print(
            line.pieces,
            line.packages,
            line.net,
            line.gross,
            "mg",
            line.measure_group,
        )

lots, _fees = build_lots(inv["lines"], pack["lines"], (spec or {}).get("lines") or [])
for lot in lots:
    if lot.get("vendor") == "MOSSLE 100":
        print(
            "lot",
            lot.get("pieces"),
            lot.get("net"),
            lot.get("gross"),
            "split",
            lot.get("split_of"),
            "mg",
            lot.get("measure_group"),
        )
