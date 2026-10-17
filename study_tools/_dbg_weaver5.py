from pathlib import Path

from prepare_transform_code.exceldoc import read_excel
from prepare_transform_code.join import build_lots
from prepare_transform_code.pdfdoc import read_pdf

folder = Path("documents/5_pravka/!Weavers")
inv = read_pdf(folder / "WEAVERS INVOICE.pdf")
pack = read_pdf(folder / "WEAVERS PLIST.pdf")
pdf_spec = read_pdf(next(folder.glob("Specification*.pdf")))
xls_spec = None
for sheet in read_excel(next(folder.glob("Specification*.xlsx"))):
    if sheet.get("lines"):
        xls_spec = sheet
        break

print("pdf first", [(l.vendor, l.pieces, l.net, l.gross, l.packages) for l in pdf_spec["lines"][:3]])
print("xls first", [(l.vendor, l.pieces, l.net, l.gross, l.packages) for l in xls_spec["lines"][:3]])

for label, spec in (("pdf", pdf_spec), ("xls", xls_spec)):
    lots, _ = build_lots(inv["lines"], pack["lines"], spec["lines"])
    missing = [lot for lot in lots if lot.get("gross") is None and not lot.get("freight")]
    pcf = sum(1 for lot in lots if lot.get("packages_conflict"))
    print(label, "missing", len(missing), "pcf", pcf)
    for lot in missing:
        print(" ", lot.get("vendor"), lot.get("pieces"), lot.get("net"), lot.get("finish"))

# both specs like analyze might extend?
lots, _ = build_lots(inv["lines"], pack["lines"], list(xls_spec["lines"]) + list(pdf_spec["lines"]))
missing = [lot for lot in lots if lot.get("gross") is None and not lot.get("freight")]
print("both missing", len(missing))
