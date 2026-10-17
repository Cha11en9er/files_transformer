from pathlib import Path

from prepare_transform_code.exceldoc import read_excel
from prepare_transform_code.fields import party_after
from prepare_transform_code.pdfdoc import read_pdf
from prepare_transform_code.shipment import (
    _address_blocks,
    _clean_address,
    _complete_address,
    _counts_for_header,
    _drop_foreign_country,
    _header_anchors,
    _party_continuation,
    _pick,
    _same_goods_sheet,
    _vendor_set,
    analyze,
)

folder = Path("documents_final/Шаблон 19")
r = analyze(folder)
print("result S", r["seller_address"][:100])
print("result B", r["buyer_address"][:100])

docs = []
for path in sorted(folder.iterdir()):
    if not path.is_file() or path.name.startswith("~$"):
        continue
    if path.suffix.lower() == ".pdf":
        doc = read_pdf(path)
        doc["name"] = path.name
        docs.append(doc)
    elif path.suffix.lower() in {".xls", ".xlsx"}:
        for sheet in read_excel(path):
            sheet["name"] = f"{path.name} / {sheet['sheet']}"
            docs.append(sheet)

invoice = _pick(docs, "invoice", True)
anchors = _header_anchors(docs)
base_vendors = _vendor_set((invoice or {}).get("lines") or [])
header_docs = [
    doc for doc in docs if _counts_for_header(doc, anchors) and _same_goods_sheet(doc, base_vendors)
]
plain = "\n".join((doc.get("raw_text") or doc.get("text") or "") for doc in header_docs)
print("header docs", [d["name"] for d in header_docs])
s, b, c = _address_blocks(plain)
print("blocks S", (s or "")[:80])
print("blocks B", (b or "")[:80])
print("blocks C", (c or "")[:80])
parties = party_after(plain)
print("parties", parties)
cont = _party_continuation(plain, parties.get("buyer") or "")
print("cont", (cont or "")[:80])
s2 = _clean_address(_complete_address(s, plain))
b2 = _clean_address(_complete_address(b, plain))
b3 = _drop_foreign_country(b2, s2)
print("after S", s2[:80])
print("after B", b3[:80])
