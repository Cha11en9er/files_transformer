import hashlib
import re
from pathlib import Path

from prepare_transform_code.exceldoc import read_excel
from prepare_transform_code.join import build_lots
from prepare_transform_code.numbers import collapse_letter_spacing, currency_of
from prepare_transform_code.pdfdoc import read_pdf

_INVOICE_NO = re.compile(
    r"INVOICE\s*NO\.?\s*:?\s*([A-Z0-9][A-Z0-9./\-]{2,})",
    re.I,
)
_CONTRACT = re.compile(
    r"CONTRACT\s*(?:NO|NR|NUMBER|#)?\.?\s*:?\s*([A-Z0-9][A-Z0-9./\-]{2,})",
    re.I,
)


def analyze(folder):
    folder = Path(folder)
    paths = [
        path
        for path in sorted(folder.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() in {".pdf", ".xls", ".xlsx", ".xlsm"}
    ]
    documents = []
    seen = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            documents.append(
                {
                    "name": path.name,
                    "role": "duplicate",
                    "duplicate_of": seen[digest],
                    "lines": [],
                    "text": "",
                    "currency": None,
                }
            )
            continue
        seen[digest] = path.name
        if path.suffix.lower() == ".pdf":
            doc = read_pdf(path)
            doc["name"] = path.name
            documents.append(doc)
        else:
            for sheet in read_excel(path):
                sheet["name"] = f"{path.name} / {sheet['sheet']}"
                documents.append(sheet)

    invoice = _pick(documents, "invoice", prefer_pdf=True)
    specification = _pick(documents, "specification", prefer_pdf=False)
    packings = [doc for doc in documents if doc["role"] == "packing" and doc["lines"]]
    weight_conflict = len(packings) > 1 and _weights_differ(packings)
    packing_lines = [] if weight_conflict or not packings else packings[0]["lines"]
    base = invoice["lines"] if invoice else (specification["lines"] if specification else [])
    lots, freights = build_lots(base, packing_lines)
    text = "\n".join(doc.get("text") or "" for doc in documents)
    text = collapse_letter_spacing(text)
    currencies = [
        doc.get("currency")
        for doc in documents
        if doc.get("currency") and doc.get("readable", True) and doc["role"] != "duplicate"
    ]
    return {
        "documents": [
            {
                "name": doc["name"],
                "role": doc["role"],
                "duplicate_of": doc.get("duplicate_of"),
                "line_count": len(doc.get("lines") or []),
            }
            for doc in documents
        ],
        "lots": lots,
        "freights": [_plain(line) for line in freights],
        "flags": ["weight_conflict"] if weight_conflict else [],
        "currency": currencies[0] if currencies else currency_of(text),
        "invoice_no": _first(_INVOICE_NO, text),
        "contract": _first(_CONTRACT, text),
    }


def _pick(documents, role, prefer_pdf):
    found = [doc for doc in documents if doc["role"] == role and doc.get("lines")]
    if not found:
        return None
    if prefer_pdf:
        pdfs = [doc for doc in found if doc.get("kind") == "pdf"]
        if pdfs:
            return pdfs[0]
    return max(found, key=lambda doc: len(doc["lines"]))


def _weights_differ(packings):
    totals = []
    for doc in packings:
        gross = sum(line.gross or 0 for line in doc["lines"] if not line.measure_group)
        totals.append(round(gross, 2))
    return len(set(totals)) > 1


def _first(pattern, text):
    folded = collapse_letter_spacing(text)
    match = pattern.search(folded)
    if not match:
        match = pattern.search(folded.upper())
    if not match:
        return None
    token = match.group(1)
    token = re.split(r"(DATE|ISSUE|CONTRACT|FROM|TO)", token, maxsplit=1)[0]
    return token.strip(".:")


def _plain(line):
    return {
        "description": line.description,
        "amount": line.amount,
        "freight": True,
    }
