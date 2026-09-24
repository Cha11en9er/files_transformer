"""Ask OpenCode (Qwen) to verify parsed shipment files. Reply must be JSON only."""

from __future__ import annotations

import base64
import json
import os
import re
import time as time_mod
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.parsing.languages import (
    detect_languages,
    detect_scripts,
    language_names,
    language_prompt_block,
)
from app.parsing.user_messages import humanize_exception, humanize_message
from app.services.field_map import is_factory_note, parse_number
from app.services.scan_examples import document_shapes

JSON_DECODER = json.JSONDecoder()
PREVIEW_LIMIT = 4000
PDF_TEXT_LIMIT = 20000
SAMPLE_ROWS = 80
TABLE_ROWS = 250
RAW_COLUMNS = 24
RAW_VALUES = 16

EXCEL_MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}
SKIP_EXCEL_ATTACH = ("сводная", "справочник", "catalog", "catalogue")
MAX_EXCEL_ATTACH_BYTES = 4 * 1024 * 1024
MAX_EXCEL_ATTACH_FILES = 8


def collect_excel_attachments(paths: list[Path] | None) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for path in paths or []:
        candidate = Path(path)
        if candidate.suffix.lower() not in EXCEL_MIME:
            continue
        name = candidate.name.lower()
        if any(token in name for token in SKIP_EXCEL_ATTACH):
            continue
        if not candidate.is_file():
            continue
        try:
            size = candidate.stat().st_size
        except OSError:
            continue
        if size <= 0 or size > MAX_EXCEL_ATTACH_BYTES:
            continue
        key = str(candidate.resolve())
        if key in seen:
            continue
        seen.add(key)
        found.append(candidate)
        if len(found) >= MAX_EXCEL_ATTACH_FILES:
            break
    return found


def to_jsonable(value: Any) -> Any:
    """Excel dates and pandas timestamps must not reach json.dumps."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if type(value).__name__ in {"NaTType", "NAType"}:
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            converted = item()
            if converted is not value:
                return to_jsonable(converted)
        except Exception:
            pass
    return str(value)

DISABLED_TOOLS = {
    "bash": False,
    "edit": False,
    "write": False,
    "read": False,
    "glob": False,
    "grep": False,
    "webfetch": False,
    "websearch": False,
    "task": False,
    "todowrite": False,
}

SYSTEM_PROMPT = (
    "You extract and verify commercial invoice / packing list / specification data from Excel tables and from scans. "
    "Each uploaded workbook is attached as JPEG screenshots of its sheets, and each PDF page as a JPEG. "
    "parser_json is the text the parser already read from those same files. "
    "parser_json is a column-mapped draft: it already keeps numbered colour children (e.g. design + colour code) "
    "separate from their unnumbered family row, copies the family unit price onto each child, and computes "
    "amount = child meters × price. For net/gross it treats the packing list as the authority for the finished "
    "weight: family packing totals are shared across children by each child's share of the sender-specification "
    "weight (not by meters), and a typical packing-vs-spec drift of about ±0.2 kg is normal. Trust that structure, "
    "but still verify. "
    "The draft can map the wrong column (TOTAL M2 is area, not amount), copy mill notes like (15+30) into the "
    "customs description, miss a letterhead date that sits only in a free-form title, or miss a line that lives "
    "only on a scan. If draft and workbook disagree, copy the workbook. "
    "Languages are not a closed list: Russian, English, Chinese, Turkish, Italian appear in the examples; "
    "Arabic or another script may arrive later. parser_json.languages names the scripts in THIS shipment. "
    "Headers like Product name / Наименование товара, DESIGN, PACKAGES, QUANTITY, Ürün Kodu, Müşteri Kodu, "
    "Net Metre, Brüt Kilogram, Sipariş No, 货号, 数量, артикул, Pattern, HIDES, Ceki, Art No. "
    "are the same fields as article / qty / meters / weight / description even when the words differ. "
    "A numbered DESIGN row may have no price; the next unnumbered SOFA FABRIC / family row holds price and summed rolls. "
    "Keep colour/article children separate from the family total row. "
    "Read every Excel sheet; skip empty sheets, date-only Sheet2, and catalog cards. "
    "Packing lists often have no black cell borders: still read every lot row and group totals "
    "(Artikel N Top, Genel Toplam). "
    "Letterhead is not only labeled cells: invoice number and invoice/shipment date may live in a title line, "
    "parentheses, or a bilingual phrase near INV / Invoice / Packing / Specification — do not confuse the "
    "shipment date with the invoice number, and do not take B/L / ETD / ETA dates as invoice_date. "
    "Customs description and TN VED usually come from a reference catalog (сводная / описание / справочник), "
    "not from an empty Annotation column; if the catalog is absent, leave description null and say so. "
    "Numbers in parser_json.context come from the workbooks. Page images confirm scans and never replace a workbook figure. "
    "If the upload is PDF/scan only, the JPEG page images and the extracted text are the source of truth: "
    "copy every goods row from every page. A table may continue on later pages without a header. "
    "A goods row is any table body line with qty/price/amount/weight/packages, even when Art No. "
    "is blank, '-', 'n/a', or a description instead of a SKU. If parser_json.items is empty, "
    "still extract those rows. "
    "Return one JSON object and nothing else. No markdown, no code fences, no reasoning, "
    "no preface, no trailing commentary. Unknown values are null. Do not invent HS/TN VED codes, "
    "prices or quantities that are not visible in the attached Excel, images or parser_json."
)

USER_PROMPT_TEMPLATE = """You get a heuristic draft (parser_json) and then JPEG screenshots: every Excel sheet of every uploaded workbook, and every PDF page. The files themselves are these images. Do not expect a raw xlsx or a raw PDF.

parser_json is a DRAFT built by column synonyms. It can be wrong: TOTAL M2 / area copied into amount, mill notes like (15+30) or (A) copied into description, comma 10,4 read as 104, missing invoice_date from a free-form title, or net/gross shared by the wrong key. Do not trust it blindly.

parser_json.context.excel is the workbook text. It is the source of truth for EVERY numeric column, not only amount: qty, unit, price, amount, rolls/packages/cartons/boxes, meters, area, net_weight, gross_weight (WEIGHT BRUTTO / BRUTTO / G.W. / GROSS WEIGHT), volume, measurement, pcs_per_carton. If draft and workbook disagree, items[] must carry the workbook number and verdict "question" with notes starting with "excel:". If packing printed a brutto/gross and the draft left gross_weight null or 0, copy the printed number. If PACKAGE/CARTONS is a separate column from QUANTITY, do not put packages into qty.

If excel_attachments is not empty, those workbooks are the source of truth for numbers. PDF/scan pages only confirm them and never override a workbook.
If there is NO Excel workbook (PDF/scan only), the attached page images and the PDF text ARE the source of truth. Copy every numbered goods row from every page. If parser_json.items is empty or much shorter than the printed table, the draft failed: fill items[] from the pages. Do not return an empty items list. A goods row still counts when MODEL/ART is "-", "n/a", blank, or the same description on every line; identity is then description (or HS + №). Two own Quantity/Amount (or two own packing qty) are two lots.

A numbered DESIGN/Art No. row is a goods line. The next unnumbered row "SOFA FABRIC / family" or "ARTIFICIAL LEATHER / family" is a group total: copy unit price from there onto each child, amount = child meters × price (not the family TOTAL M2).
PACKAGES / PACKAGE / CARTONS / CTNS is rolls or boxes (places), never commercial Quantity. QUANTITY is pcs/sets/meters. HIDES is leather pieces; Pattern on a DPL sheet is the article.
Two lots of the same Art No. stay two items[] only when the article is written again as its own cell. A merged Art No. block with extra Quantity/Amount rows is ONE item number: keep those rows as lots[] / continuation lines, do not emit extra items[] and do not invent a new No.
Qty/price/amount stretched by merge across packing-only rows is also one commercial line: keep packing lines, sum own packing numbers.
Do not copy a merged net/gross/cartons block onto a neighbouring Art No. that only inherited those cells.
Stop at the printed TOTAL. "Detail packing list" and a new header after TOTAL are component packing of already listed articles (hyphen-suffix SKU belongs to the parent already in items[]), not extra invoice goods lines.
A goods table may span many pages. Later pages often continue the same columns without a header, or with a repeated/partial header. Do not stop after page 1. Read every entry in images_manifest. Keep item numbers increasing until the printed TOTAL. A stamp or signature may sit on the totals footer; ignore the stamp, still copy TOTAL numbers, and keep all goods rows above it.
Headers may be two stacked rows: a group title (WEIGHT, COUNTRY) plus subheaders (NETTO / NETTO WITH PRIMARY PACKAGING / BRUTTO, OF ORIGIN). MODEL / SERIES / ART. is the article. PACKAGE is places (rolls/boxes), never Quantity.
Do not emit header leftovers (SERIES / ART., BRAND, NETTO, kg) as items[].
parser_json may have dropped continuation-page rows. If a numbered goods line is visible on a later page and missing from parser_json.items, add it with verdict "extra".
Skip letterhead rows (Terms of delivery/payment, bank, director, address) — they are not items.
Skip empty sheets, date-only Sheet2, and catalog/card sheets (справочник, 1601057).
The output field "description" is the customs Product name / Наименование товара, not mill cutting notes and not the article.
Packing DESIGN "category / family" (SOFA FABRIC / Sherlock) is the family key. Net/gross on that packing row are the commercial weights for the finished export. Summed sender-specification roll weights are a cross-check: about ±0.2 kg drift is usual; if they differ more, keep packing and note it. When one packing family covers several colour children, share packing net/gross by each child's sender-spec weight share (fallback: meters), not by inventing new totals.
Catalog sheets may fill description and tnved only on an exact article match. Do not invent bilingual customs text. If no catalog is attached and the goods sheet has no real product-name column, description stays null.

Letterhead / header (flexible — titles differ by supplier):
- invoice_no: value next to Inv No / Invoice No / INV.NO / инв номер / similar — not a date in parentheses.
- invoice_date: the date next to Date / Invoice date, or the date on the specification/invoice number line ("№ … от 02.04.2026", "dd …"). MAY.20.2026 is 20 May 2026. A fragment inside the invoice number (EXD4-26-095) is not a date. The Contract dated line is contract_date, not invoice_date. Delivery date / сроки поставки / not later than is delivery_date. B/L, ETD, ETA, sailing are not invoice_date.
- currency: from the price/amount column (USD, $, EUR). A street abbreviation "Cad." (cadde) is not the CAD currency.
- contract_date: only the date on the Contract / Контракт line (dd / dated / от). The date after Specification / Спецификация № is the spec date, not the contract date.
- contract_no / container: label or the adjacent cell, any language.
- buyer and seller: the company under its own label. Buyer and Seller on one row own the columns below them, not the cell to the right. "TO: Messrs" / Attn is a salutation, the company on the next line is the buyer. Do not put the buyer's address into the seller.
- seller: THE SELLER / Seller / Shipper, or the company in the letterhead above TO/Buyer.
- manufacturer: an explicit Manufacturer / Производитель label, even when that company is also the seller. Never invent a manufacturer by copying the seller when the document has no such label.
- currency: from PRICE PER / Amount column titles (USD, CNY/RMB, EUR). Payment text that lists several allowed currencies ("yuan, US dollars") is NOT the invoice currency.
If parser_json.header missed a field that is visible in the workbook letterhead, fill header[] and mention "excel:header" in notes on any related item or in meaning.

languages_in_this_shipment (detected scripts + encodings; a new language is still valid):
{language_notes}

source_files: parser read of each uploaded file (Excel sheets with column names and sample rows; PDF text/OCR if any).
parser_json.items: unified DRAFT rows. Compare them to attached Excel.
parser_json.excel_totals: sums of the draft rows. Compare to printed TOTAL on Excel and on a scan — qty, amount, rolls/cartons, net_weight AND gross_weight, volume, area. Never drop a printed TOTAL column because the draft left it empty.
parser_json.excel_attachments: filenames of attached workbooks.
images_manifest: each attached JPEG is a screenshot of a PDF/scan page.

document_shapes:
{document_shapes}

parser_json:
{parser_json}

Reply with this exact JSON shape:
{{
  "meaning": "what you read (excel invoice+packing+spec, signed invoice scan, mixed, not the goods document)",
  "header": {{
    "buyer": null,
    "seller": null,
    "manufacturer": null,
    "currency": null,
    "invoice_no": null,
    "invoice_date": null,
    "contract_no": null,
    "delivery_terms": null,
    "container": null
  }},
  "tables": [
    {{
      "role": "goods",
      "page": 1,
      "source": "filename.xlsx",
      "why": "line items with article, qty, amount",
      "columns": {{
        "article": "printed header",
        "qty": "printed header",
        "description": "Product name / Наименование товара",
        "amount": "printed header",
        "net_weight": "printed header",
        "gross_weight": "printed header (WEIGHT BRUTTO / G.W. / GROSS)",
        "rolls": "PACKAGE / CARTONS header if any"
      }}
    }}
  ],
  "totals": {{
    "qty": null,
    "meters": null,
    "amount": null,
    "rolls": null,
    "boxes": null,
    "net_weight": null,
    "gross_weight": null,
    "area": null,
    "volume": null
  }},
  "items": [
    {{
      "article": "",
      "qty": null,
      "meters": null,
      "unit": null,
      "rolls": null,
      "boxes": null,
      "price": null,
      "amount": null,
      "net_weight": null,
      "gross_weight": null,
      "area": null,
      "volume": null,
      "measurement": null,
      "color": null,
      "description": null,
      "lots": null,
      "verdict": "ok",
      "notes": null
    }}
  ]
}}

Rules:
- For each Excel file in source_files list tables[].source = that filename, role goods/packing/totals/ignored, and columns as internal field -> printed header. Mention PACKAGES vs ROLLS, QUANTITY vs METERS, TOTAL M2 vs AMOUNT, WEIGHT BRUTTO vs NET.
- On every screenshot find ALL tables, including borderless ones. Classify each: goods, packing, totals, ignored.
- On Weavers-style lines the article is the Design Name between slashes (DYER 789), not the whole blob and not HS CODE.
- On Tosun invoice the article is the fabric name before metres (ZIMMY, SINDRI), even if spaces were lost in OCR (ZIMMY1.740,82 MT).
- If two goods-like tables exist and parser_json.items is not empty, pick the one whose articles overlap parser_json.items. Put the other in tables[] with role "ignored" and why. If parser_json.items is empty, take the goods table from the pages/workbook as-is.
- Always copy the printed document TOTAL into totals, including gross_weight / brutto and cartons when printed. Never drop TOTAL. Do not put the TOTAL row into items[].
- items[] follow parser_json.items when articles match, but numbers come from attached Excel when they differ. verdict: ok if they match the workbook, question if you corrected the draft, extra if in the file but not in parser_json, missing if in parser_json but not in Excel.
- Put every goods row from Excel into items[]. Continuation rows inside a merged Art No. stay lots[] of that item, not extra items[]. lots[] is a list of {{qty, price, amount, color}} or null when the item is a single commercial line. If the draft dropped a line whose Art No. is written again as its own cell, emit extra rows. Do not invent bilingual descriptions that are not in Excel or the catalog. Catalog names often use EN//RU - copy both sides, do not leave a leading slash.
- qty is commercial quantity in unit. meters is packing meters. Amount is money, never m2. rolls/boxes are places. gross_weight is brutto, net_weight is netto.
- description is customs Product name from Excel or catalog, or null. Never copy (15+30), (A), 0605 Special Order, and never invent text because an etalon once had it.
- net_weight / gross_weight / volume / boxes / rolls: prefer packing-list commercial numbers; if draft used sender-spec rolls and packing disagrees beyond ~0.2 kg, correct to packing and verdict "question" with notes like "excel:weight packing vs spec". If WEIGHT BRUTTO / GROSS WEIGHT is a printed column, gross_weight must not stay null.
- header.invoice_date and header.invoice_no: fill from letterhead even when the draft left them empty; do not put a shipment or delivery date into invoice_date, and do not put a date fragment of the invoice number into invoice_date.
- header.contract_date comes only from the Contract line. header.buyer_address is the address under Buyer, header.seller_address under Seller.
- header.seller is the trading party; header.manufacturer is the labeled Manufacturer / Производитель. If the document names the same company as both, keep both. If there is no manufacturer label, leave manufacturer null.
- items[].article includes the colour code when it is a separate column (MAXWELL + 997 -> "MAXWELL 997"). Do not collapse those packing rows into one family line unless the packing list itself printed one family total.
- header.currency is the invoice price currency from PRICE/AMOUNT column titles (USD/EUR/CNY/GBP/TRY/AED/SAR and other ISO). Do not set CNY just because payment terms mention yuan among other options. If USD and TRY both appear, keep the commercial amount currency (usually USD). Pounds, lire, dinars, dirhams are real currencies — copy the printed ISO, do not coerce them to USD.
- If HS / TNVED is printed with dots or spaces (54.07.73.00.90.11, 59.03.10.90.10.00) or as a 13-digit Excel float, strip dots and keep digits. TNVED is at most 10 digits. Do not invent a code that is not printed and not in the catalog. A 3-5 digit PO under the header is not HS.
- A smashed PDF header (letters from two alphabets in one word) is not a column title. Rebuild the row from the visible table. If the article cell is ОТСУТСТВУЕТ, n/a, or a torn piece of the description, leave article null and keep the full description.
- Skip junk rows anywhere (under the header, middle, end): lone PO numbers, translation header lines, page marks. Two DESIGN columns: text=article, numbers=color. AMOUNT (M) and METRS are meters. UNIT PICE is unit price. Packing article is Customer Name / Müşteri Kodu. m2 = meters × width when missing. QC/certificate sheets are not goods. описание qty=1 is catalog, not a second lot.
- PDF letter/blob lines without a table still hold goods: "qty description –DESIGN price amount USD" and "CODE / DESIGN NAME / mill / … qty MT price$ amount$". Read page text when parser_json.items is empty or the table is only a TOTAL footer.
- Numeric fields are numbers, not strings. notes is a short fact, or null. Prefix notes with "excel:" when you corrected the draft from a workbook.
- If the scan is a different document than parser_json (no article overlap), say so in meaning, put scan lines as extra, do not force-match.
"""

TOTAL_ROW_NAMES = {
    "total",
    "totals",
    "grand total",
    "total quantity",
    "total amount",
    "итого",
    "всего",
    "合计",
    "总计",
}

TOTAL_ALIASES = {
    "qty": ("qty", "quantity", "total_qty", "total_quantity", "qty_total"),
    "meters": ("meters", "m", "total_meters", "meter"),
    "amount": ("amount", "total_amount", "total", "sum", "total_sum"),
    "rolls": ("rolls", "packages", "pkgs", "package", "total_rolls"),
    "boxes": ("boxes", "cartons", "ctns", "carton", "total_cartons"),
    "net_weight": ("net_weight", "n_w", "nw", "net", "netto", "total_net", "weight_netto"),
    "gross_weight": (
        "gross_weight",
        "g_w",
        "gw",
        "gross",
        "brutto",
        "total_gross",
        "weight_brutto",
        "weight brutto",
    ),
    "area": ("area", "sqm", "m2", "total_area"),
    "volume": ("volume", "cbm", "m3", "total_volume"),
}


def _load_dotenv() -> None:
    root = Path(__file__).resolve().parents[2]
    for candidate in (Path.cwd() / ".env", root / ".env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv()


def settings() -> dict[str, Any]:
    model = os.getenv("OPENCODE_MODEL", "opencode/qwen3.5-plus").strip()
    provider, _, model_id = model.partition("/")
    if not model_id:
        provider, model_id = "opencode", provider or "qwen3.5-plus"
    enabled = os.getenv("OPENCODE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    return {
        "url": os.getenv("OPENCODE_URL", "http://127.0.0.1:4096").rstrip("/"),
        "username": os.getenv("OPENCODE_SERVER_USERNAME", "opencode"),
        "password": os.getenv("OPENCODE_SERVER_PASSWORD", ""),
        "model": f"{provider}/{model_id}",
        "provider_id": provider,
        "model_id": model_id,
        "enabled": enabled,
        "timeout_s": float(os.getenv("OPENCODE_TIMEOUT_S", "180")),
    }


OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"


def _usd_text(value: float | None) -> str:
    if value is None:
        return ""
    return f"${value:.2f}"


def _model_label(model: str) -> str:
    slug = (model or "").rsplit("/", 1)[-1]
    chunks: list[str] = []
    for part in slug.replace("_", "-").split("-"):
        if not part:
            continue
        lower = part.lower()
        if lower.startswith("qwen"):
            chunks.append("Qwen" + part[4:])
        elif lower.isdigit():
            chunks.append(part)
        else:
            chunks.append(part[:1].upper() + part[1:])
    return " ".join(chunks) or (model or "модель")


def _empty_billing() -> dict[str, Any]:
    return {
        "usage_usd": None,
        "remaining_usd": None,
        "purchased_usd": None,
        "remaining_is_key_limit": False,
        "billing_error": None,
    }


def _extract_openrouter_key(blob: Any) -> str:
    if isinstance(blob, dict):
        block = blob.get("openrouter")
        if isinstance(block, dict):
            for name in ("key", "apiKey", "api_key", "token"):
                value = str(block.get(name) or "").strip()
                if value.startswith("sk-or-"):
                    return value
        for value in blob.values():
            found = _extract_openrouter_key(value)
            if found:
                return found
    elif isinstance(blob, list):
        for item in blob:
            found = _extract_openrouter_key(item)
            if found:
                return found
    return ""


def _openrouter_api_key() -> str:
    env = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if env.startswith("sk-or-"):
        return env
    home = Path(os.getenv("HOME") or Path.home())
    paths = [
        home / ".local" / "share" / "opencode" / "auth.json",
        home / ".opencode" / "auth.json",
    ]
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        paths.insert(0, Path(xdg) / "opencode" / "auth.json")
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        found = _extract_openrouter_key(data)
        if found:
            return found
    return ""


def fetch_openrouter_billing() -> dict[str, Any]:
    """Account/key spend from OpenRouter. Never returns the API key."""
    out = _empty_billing()
    if settings()["provider_id"] != "openrouter":
        return out
    key = _openrouter_api_key()
    mgmt = (os.getenv("OPENROUTER_MANAGEMENT_KEY") or "").strip()
    if not key and not mgmt:
        out["billing_error"] = "нет ключа OpenRouter"
        return out
    try:
        with httpx.Client(timeout=httpx.Timeout(6.0, connect=3.0)) as client:
            if key:
                reply = client.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key}"})
                if reply.status_code == 200:
                    data = (reply.json() or {}).get("data") or {}
                    out["usage_usd"] = _as_float(data.get("usage"))
                    remaining = _as_float(data.get("limit_remaining"))
                    if remaining is not None:
                        out["remaining_usd"] = remaining
                        out["remaining_is_key_limit"] = True
            token = mgmt or key
            credits = client.get(OPENROUTER_CREDITS_URL, headers={"Authorization": f"Bearer {token}"})
            if credits.status_code == 200:
                data = (credits.json() or {}).get("data") or {}
                purchased = _as_float(data.get("total_credits"))
                used = _as_float(data.get("total_usage"))
                out["purchased_usd"] = purchased
                if purchased is not None and used is not None:
                    out["remaining_usd"] = round(purchased - used, 6)
                    out["remaining_is_key_limit"] = False
                    if out["usage_usd"] is None:
                        out["usage_usd"] = used
            elif credits.status_code >= 400 and out["usage_usd"] is None:
                out["billing_error"] = humanize_message(credits.text[:200])
    except Exception as exc:
        out["billing_error"] = humanize_exception(exc)
    return out


def _usage_delta(before: dict[str, Any], after: dict[str, Any]) -> float | None:
    start = before.get("usage_usd")
    end = after.get("usage_usd")
    if start is None or end is None:
        return None
    return round(max(0.0, float(end) - float(start)), 6)


def _with_billing(payload: dict[str, Any]) -> dict[str, Any]:
    cfg = settings()
    billing = fetch_openrouter_billing() if cfg["provider_id"] == "openrouter" else _empty_billing()
    payload.update(billing)
    payload["model"] = cfg["model"]
    payload["model_label"] = _model_label(cfg["model"])
    remaining = _usd_text(billing.get("remaining_usd"))
    usage = _usd_text(billing.get("usage_usd"))
    label = payload["model_label"]
    if payload.get("status") == "ok":
        if remaining and not billing.get("remaining_is_key_limit"):
            payload["title"] = f"{label} · остаток {remaining}"
        elif remaining:
            payload["title"] = f"{label} · лимит ключа {remaining}"
        elif usage:
            payload["title"] = f"{label} · расход {usage}"
        else:
            payload["title"] = f"{label} · жива"
    money_bits: list[str] = []
    if usage:
        money_bits.append(f"расход {usage}")
    if remaining:
        noun = "лимит ключа" if billing.get("remaining_is_key_limit") else "остаток"
        money_bits.append(f"{noun} {remaining}")
    payload["money"] = " · ".join(money_bits)
    detail = payload.get("detail") or ""
    if billing.get("billing_error") and not money_bits:
        payload["detail"] = f"{detail} {billing['billing_error']}".strip()
    elif money_bits:
        payload["detail"] = f"{detail} · {payload['money']}".strip(" ·")
    return payload


def probe_opencode() -> dict[str, Any]:
    """Cheap liveness check for the header indicator. Never returns secrets."""
    cfg = settings()
    model = cfg["model"]
    if not cfg["enabled"]:
        payload = {
            "status": "off",
            "title": "Модель выключена",
            "detail": "В .env стоит OPENCODE_ENABLED=0.",
            "model": model,
            "model_label": _model_label(model),
            "money": "",
        }
        payload.update(_empty_billing())
        return payload
    auth = (cfg["username"], cfg["password"]) if cfg["password"] else None
    last_error = "нет ответа"
    try:
        with httpx.Client(base_url=cfg["url"], auth=auth, timeout=httpx.Timeout(3.0, connect=2.0)) as client:
            for path in ("/global/health", "/session", "/doc", "/"):
                try:
                    reply = client.get(path)
                except Exception as exc:
                    last_error = humanize_exception(exc)
                    continue
                if reply.status_code in {200, 204, 404, 405}:
                    return _with_billing({
                        "status": "ok",
                        "title": "Модель жива",
                        "detail": f"{cfg['url']} · {model}",
                        "model": model,
                    })
                if reply.status_code in {401, 403}:
                    if not cfg["password"]:
                        return _with_billing({
                            "status": "auth",
                            "title": "Нет пароля в .env",
                            "detail": "OpenCode на 4096 уже работает, но ждёт OPENCODE_SERVER_PASSWORD из backend/.env.",
                            "model": model,
                        })
                    return _with_billing({
                        "status": "auth",
                        "title": "Пароль не подошёл",
                        "detail": "Пароль в backend/.env не совпадает с паролем службы opencode.",
                        "model": model,
                    })
                last_error = humanize_message(f"{reply.status_code}: {reply.text[:200]}")
    except Exception as exc:
        last_error = humanize_exception(exc)
    return _with_billing({
        "status": "down",
        "title": "Модель не отвечает",
        "detail": last_error,
        "model": model,
    })


HELLO_PROMPT = (
    "Привет. Что ты за модель? Ответь одним коротким предложением на русском, без рассуждений."
)


def _looks_like_credits(text: str) -> bool:
    low = (text or "").lower()
    return any(
        marker in low
        for marker in (
            "creditserror",
            "insufficient balance",
            "no payment method",
            "free usage exceeded",
        )
    )


def ping_opencode(prompt: str | None = None) -> dict[str, Any]:
    """One short completion so we can tell auth from Zen billing. No secrets."""
    live = probe_opencode()
    cfg = settings()
    result: dict[str, Any] = {
        "status": live["status"],
        "title": live["title"],
        "detail": live["detail"],
        "model": cfg["model"],
        "reply": None,
    }
    if live["status"] != "ok":
        for key in ("money", "model_label", "usage_usd", "remaining_usd", "purchased_usd", "remaining_is_key_limit", "billing_error"):
            result[key] = live.get(key)
        return result
    auth = (cfg["username"], cfg["password"]) if cfg["password"] else None
    session_id: str | None = None
    asked = (prompt or HELLO_PROMPT).strip()
    try:
        with httpx.Client(base_url=cfg["url"], auth=auth, timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            created = client.post("/session", json={"title": "ping"})
            created.raise_for_status()
            body = created.json()
            session_id = body.get("id") or body.get("sessionID") or (body.get("info") or {}).get("id")
            if not session_id:
                raise RuntimeError(f"нет id сессии: {body}")
            encoded_id = quote(str(session_id), safe="")
            reply = client.post(
                f"/session/{encoded_id}/message",
                json={
                    "system": "Answer in one short Russian sentence. No markdown.",
                    "model": {"providerID": cfg["provider_id"], "modelID": cfg["model_id"]},
                    "tools": DISABLED_TOOLS,
                    "parts": [{"type": "text", "text": asked}],
                },
            )
            raw_body = reply.text[:800]
            if reply.status_code >= 400:
                text = humanize_message(raw_body)
                result["reply"] = raw_body
                result["status"] = "credits" if _looks_like_credits(raw_body) else "error"
                result["title"] = "Нет баланса Zen" if result["status"] == "credits" else "Модель вернула ошибку"
                result["detail"] = text
                return _with_billing(result)
            raw_text = _assistant_text(reply.json()) or raw_body
            if _looks_like_credits(raw_text):
                result["status"] = "credits"
                result["title"] = "Нет баланса Zen"
                result["detail"] = humanize_message(raw_text)
                result["reply"] = raw_text[:800]
                return _with_billing(result)
            result["status"] = "ok"
            result["title"] = "Модель ответила"
            result["detail"] = (raw_text or "пустой ответ")[:300]
            result["reply"] = (raw_text or "")[:800]
            billed = _with_billing(result)
            billed["reply"] = result["reply"]
            return billed
    except httpx.HTTPStatusError as exc:
        body = (exc.response.text or str(exc))[:800]
        result["status"] = "credits" if _looks_like_credits(body) else "error"
        result["title"] = "Нет баланса Zen" if result["status"] == "credits" else "Модель вернула ошибку"
        result["detail"] = humanize_message(body)
        result["reply"] = body
        return _with_billing(result)
    except Exception as exc:
        result["status"] = "error"
        result["title"] = "Модель вернула ошибку"
        result["detail"] = humanize_exception(exc)
        result["reply"] = str(exc)[:400]
        return _with_billing(result)
    finally:
        if session_id:
            try:
                with httpx.Client(base_url=cfg["url"], auth=auth, timeout=8.0) as client:
                    client.delete(f"/session/{quote(str(session_id), safe='')}")
            except Exception:
                pass


def _doc_type_value(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _line_raw(line: Any) -> dict[str, Any]:
    if isinstance(line, dict):
        return line.get("raw") or {}
    return getattr(line, "raw", None) or {}


def _base_filename(filename: str | None, file_path: str | None = None) -> str:
    name = filename or ""
    if " · " in name:
        return name.split(" · ", 1)[0]
    if name:
        return Path(name).name
    if file_path:
        return Path(file_path).name
    return name


def _file_kind(mime_hint: str | None, filename: str | None) -> str:
    mime = (mime_hint or "").lower()
    if mime in {"pdf", "image", "excel"}:
        return "pdf" if mime == "image" else mime
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        return "pdf"
    return "excel"


def _line_table_row(line: Any) -> dict[str, Any]:
    from app.services.field_map import map_row

    raw = _line_raw(line)
    mapped = map_row(raw)
    article = line.get("article") if isinstance(line, dict) else getattr(line, "article", None)
    return {
        "article": article or mapped.get("article"),
        "qty": mapped.get("qty"),
        "unit": mapped.get("unit"),
        "rolls": mapped.get("rolls"),
        "meters": mapped.get("meters"),
        "price": mapped.get("price"),
        "amount": mapped.get("amount"),
        "net_weight": mapped.get("net_weight"),
        "gross_weight": mapped.get("gross_weight"),
        "hs_code": mapped.get("hs_code") or mapped.get("customs_code"),
        "customs_code": mapped.get("customs_code") or mapped.get("hs_code"),
        "country": mapped.get("country"),
        "manufacturer": mapped.get("manufacturer"),
        "description": mapped.get("description") or mapped.get("description_en") or mapped.get("description_ru"),
        "raw": {
            str(key): to_jsonable(raw[key])
            for key in list(raw.keys())[:RAW_VALUES]
            if raw.get(key) not in (None, "")
        },
    }


def compact_source_files(parsed_docs: list[Any] | None) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for doc in parsed_docs or []:
        if isinstance(doc, dict):
            filename = doc.get("filename")
            doc_type = _doc_type_value(doc.get("doc_type"))
            sheets = list(doc.get("sheets") or [])
            preview = str(doc.get("text_preview") or "")
            mime_hint = doc.get("mime_hint")
            lines = doc.get("lines") or []
        else:
            filename = doc.filename
            doc_type = _doc_type_value(doc.doc_type)
            sheets = list(doc.sheets or [])
            preview = str(doc.text_preview or "")
            mime_hint = doc.mime_hint
            lines = doc.lines or []
        sample: list[dict[str, Any]] = []
        for line in lines[:SAMPLE_ROWS]:
            raw = _line_raw(line)
            article = line.get("article") if isinstance(line, dict) else getattr(line, "article", None)
            row_index = line.get("row_index") if isinstance(line, dict) else getattr(line, "row_index", None)
            columns = [str(key) for key in list(raw.keys())[:RAW_COLUMNS]]
            values = {str(key): to_jsonable(raw[key]) for key in list(raw.keys())[:RAW_VALUES]}
            sample.append(
                {
                    "row": row_index,
                    "article": article,
                    "columns": columns,
                    "values": values,
                }
            )
        preview_cut = preview[: (PDF_TEXT_LIMIT if _file_kind(mime_hint, filename) == "pdf" else PREVIEW_LIMIT)]
        lang_blob = " ".join(
            [
                str(filename or ""),
                " ".join(str(sheet) for sheet in sheets),
                preview_cut,
            ]
        )
        files.append(
            {
                "filename": filename,
                "doc_type": doc_type,
                "mime_hint": mime_hint,
                "kind": _file_kind(mime_hint, filename),
                "sheets": sheets,
                "line_count": len(lines),
                "languages": language_names(detect_languages(lang_blob)),
                "scripts": detect_scripts(lang_blob),
                "preview": preview_cut,
                "sample_rows": sample,
                "table": [_line_table_row(line) for line in lines[:SAMPLE_ROWS]],
            }
        )
    return files


def build_operator_context(
    parsed_docs: list[Any] | None = None,
    pages: list[VisionPage] | None = None,
) -> dict[str, Any]:
    """What the operator sees: parser text and tables sent toward the model."""
    grouped: dict[str, dict[str, Any]] = {}
    for doc in parsed_docs or []:
        if isinstance(doc, dict):
            filename = doc.get("filename")
            file_path = doc.get("file_path")
            doc_type = _doc_type_value(doc.get("doc_type"))
            sheets = list(doc.get("sheets") or [])
            preview = str(doc.get("text_preview") or "")
            mime_hint = doc.get("mime_hint")
            lines = doc.get("lines") or []
        else:
            filename = doc.filename
            file_path = doc.file_path
            doc_type = _doc_type_value(doc.doc_type)
            sheets = list(doc.sheets or [])
            preview = str(doc.text_preview or "")
            mime_hint = doc.mime_hint
            lines = doc.lines or []
        kind = _file_kind(mime_hint, filename)
        key = _base_filename(filename, file_path)
        bucket = grouped.setdefault(
            key,
            {
                "filename": key,
                "kind": kind,
                "doc_type": doc_type,
                "sheets": [],
                "line_count": 0,
                "text": "",
                "table": [],
                "pages": [],
            },
        )
        for sheet in sheets:
            if sheet and sheet not in bucket["sheets"]:
                bucket["sheets"].append(sheet)
        bucket["line_count"] += len(lines)
        if preview:
            extra = preview if not bucket["text"] else f"\n\n{preview}"
            bucket["text"] = (bucket["text"] + extra)[:PDF_TEXT_LIMIT]
        bucket["table"].extend(_line_table_row(line) for line in lines[:TABLE_ROWS])
        if not bucket.get("doc_type"):
            bucket["doc_type"] = doc_type
    pages_by_source: dict[str, list[int]] = {}
    for page in pages or []:
        pages_by_source.setdefault(page.source_name, []).append(page.page or 0)
        if page.source_name not in grouped:
            grouped[page.source_name] = {
                "filename": page.source_name,
                "kind": "pdf",
                "doc_type": None,
                "sheets": [],
                "line_count": 0,
                "text": "",
                "table": [],
                "pages": [],
            }
    for name, page_nos in pages_by_source.items():
        grouped[name]["pages"] = sorted({n for n in page_nos if n})
        grouped[name]["kind"] = "pdf"
    excel: list[dict[str, Any]] = []
    pdfs: list[dict[str, Any]] = []
    for item in grouped.values():
        n = item["line_count"]
        sheets = ", ".join(item["sheets"][:8])
        if item["kind"] == "pdf":
            pages_label = ", ".join(str(p) for p in item["pages"]) or "-"
            item["meaning"] = f"{n} строк · стр. {pages_label}"
            pdfs.append(item)
        else:
            item["meaning"] = (
                (f"листы: {sheets}. " if sheets else "")
                + f"{n} строк"
            )
            excel.append(item)
    return {"excel": excel, "pdfs": pdfs}


def compact_image_manifest(pages: list[VisionPage] | None) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for page in pages or []:
        manifest.append(
            {
                "filename": page.path.name,
                "source": page.source_name,
                "page": page.page,
            }
        )
    return manifest


def compact_parser_snapshot(
    *,
    title: str,
    profile_type: str,
    files: list[dict[str, Any]],
    header_fields: dict[str, Any],
    items: list[dict[str, Any]],
    parsed_docs: list[Any] | None = None,
    pages: list[VisionPage] | None = None,
    excel_totals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compact_items: list[dict[str, Any]] = []
    for item in items[:250]:
        commercial = item.get("commercial_data") or {}
        packing = item.get("packing_data") or {}
        customs = item.get("customs_data") or {}
        flags = [
            err.get("message") or err.get("error_type")
            for err in (item.get("validation_errors") or [])
            if not err.get("resolved")
        ]
        compact_items.append(
            {
                "article": item.get("article"),
                "qty": commercial.get("qty"),
                "lots": commercial.get("lots") or None,
                "unit": commercial.get("unit"),
                "color": commercial.get("color"),
                "rolls": packing.get("rolls"),
                "boxes": packing.get("boxes"),
                "meters": packing.get("meters"),
                "width": packing.get("width"),
                "area": packing.get("area"),
                "net_weight": packing.get("net_weight"),
                "gross_weight": packing.get("gross_weight"),
                "volume": packing.get("volume"),
                "measurement": packing.get("measurement"),
                "pcs_per_carton": packing.get("pcs_per_carton"),
                "price": commercial.get("price"),
                "amount": commercial.get("amount"),
                "hs_code": customs.get("hs_code"),
                "tnved_code": customs.get("tnved_code"),
                "description": customs.get("description")
                or customs.get("description_ru")
                or customs.get("description_en"),
                "flags": flags[:4],
            }
        )
    source_files = compact_source_files(parsed_docs)
    payload = {
        "title": title,
        "profile_type": profile_type,
        "files": [
            {
                "filename": f.get("filename"),
                "doc_type": f.get("doc_type"),
                "parse_status": f.get("parse_status"),
            }
            for f in files[:40]
        ],
        "source_files": source_files,
        "images_manifest": compact_image_manifest(pages),
        "header": {k: v for k, v in (header_fields or {}).items() if v not in (None, "", [])},
        "excel_totals": excel_totals or {},
        "excel_attachments": [
            Path(
                (doc.get("file_path") or doc.get("filename"))
                if isinstance(doc, dict)
                else (getattr(doc, "file_path", None) or getattr(doc, "filename", None) or "")
            ).name
            for doc in (parsed_docs or [])
            if (
                (doc.get("file_path") or doc.get("filename"))
                if isinstance(doc, dict)
                else (getattr(doc, "file_path", None) or getattr(doc, "filename", None))
            )
        ],
        "items": compact_items,
        "context": build_operator_context(parsed_docs, pages),
    }
    lang_blob = _snapshot_language_blob(payload)
    ids = detect_languages(lang_blob)
    payload["languages"] = {
        "ids": ids,
        "names": language_names(ids),
        "scripts": detect_scripts(lang_blob),
        "notes": language_prompt_block(lang_blob),
    }
    return to_jsonable(payload)


def _snapshot_language_blob(snapshot: dict[str, Any]) -> str:
    parts: list[str] = [str(snapshot.get("title") or "")]
    header = snapshot.get("header") or {}
    parts.extend(str(value) for value in header.values() if value not in (None, ""))
    for item in snapshot.get("items") or []:
        parts.append(str(item.get("article") or ""))
        parts.append(str(item.get("description") or ""))
    for doc in snapshot.get("source_files") or []:
        parts.append(str(doc.get("filename") or ""))
        parts.extend(str(sheet) for sheet in (doc.get("sheets") or [])[:12])
        parts.append(str(doc.get("preview") or "")[:3000])
        for row in (doc.get("sample_rows") or [])[:8]:
            parts.append(str(row.get("article") or ""))
            parts.extend(str(col) for col in (row.get("columns") or [])[:12])
    return "\n".join(parts)


def build_user_prompt(snapshot: dict[str, Any]) -> str:
    notes = (snapshot.get("languages") or {}).get("notes") or language_prompt_block(
        _snapshot_language_blob(snapshot)
    )
    return USER_PROMPT_TEMPLATE.format(
        document_shapes=document_shapes(),
        language_notes=notes,
        parser_json=json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
    )


def extract_json_payload(text: str) -> Any:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```.*$", "", cleaned, flags=re.S)
    for index, char in enumerate(cleaned):
        if char not in "{[":
            continue
        try:
            obj, _end = JSON_DECODER.raw_decode(cleaned[index:])
            return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("модель не вернула JSON")


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    return parse_number(value)


def _pick_alias(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    lower = {str(key).lower(): value for key, value in data.items()}
    for name in names:
        if name in lower and lower[name] not in (None, ""):
            return lower[name]
    return None


def coerce_totals(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    return {field: _as_float(_pick_alias(data, aliases)) for field, aliases in TOTAL_ALIASES.items()}


def _is_total_row(article: str | None) -> bool:
    text = (article or "").strip().lower()
    return text in TOTAL_ROW_NAMES or text.startswith("total ")


def coerce_scan_item(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    rolls = _as_float(data.get("rolls"))
    if rolls is None:
        rolls = _as_float(data.get("packages") if data.get("packages") is not None else data.get("package"))
    boxes = _as_float(data.get("boxes") if data.get("boxes") is not None else data.get("cartons"))
    gross = _as_float(data.get("gross_weight"))
    if gross is None:
        gross = _as_float(data.get("brutto") if data.get("brutto") is not None else data.get("gross"))
    net = _as_float(data.get("net_weight"))
    if net is None:
        net = _as_float(data.get("netto") if data.get("netto") is not None else data.get("net"))
    return {
        "article": data.get("article") or data.get("model"),
        "matched_article": data.get("matched_article"),
        "item_id": data.get("item_id"),
        "qty": _as_float(data.get("qty") if data.get("qty") is not None else data.get("quantity")),
        "meters": _as_float(data.get("meters")),
        "unit": data.get("unit"),
        "rolls": rolls,
        "boxes": boxes,
        "price": _as_float(data.get("price")),
        "amount": _as_float(data.get("amount")),
        "currency": data.get("currency"),
        "manufacturer": data.get("manufacturer"),
        "net_weight": net,
        "gross_weight": gross,
        "area": _as_float(data.get("area")),
        "volume": _as_float(data.get("volume")),
        "measurement": data.get("measurement"),
        "color": data.get("color"),
        "description": None if is_factory_note(data.get("description")) else (
            data.get("description") or data.get("description_en") or data.get("description_ru")
        ),
        "verdict": data.get("verdict") or "question",
        "notes": data.get("notes"),
    }


def coerce_scan_table(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    role = str(data.get("role") or "ignored").strip().lower()
    if role not in {"goods", "totals", "ignored"}:
        role = "ignored"
    columns = data.get("columns") if isinstance(data.get("columns"), dict) else {}
    page = data.get("page")
    if isinstance(page, str):
        try:
            page = int(float(page))
        except ValueError:
            page = None
    return {
        "role": role,
        "page": page,
        "source": data.get("source"),
        "why": data.get("why"),
        "columns": {str(key): str(value) for key, value in columns.items() if value not in (None, "")},
    }


def normalize_model_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "meaning": None,
            "header": {},
            "tables": [],
            "totals": coerce_totals({}),
            "items": [],
            "payload": raw,
        }
    items_in = list(raw.get("items") or [])
    items: list[dict[str, Any]] = []
    totals_from_row: dict[str, Any] = {}
    for row in items_in:
        article = (row or {}).get("article") if isinstance(row, dict) else None
        if _is_total_row(article):
            totals_from_row = coerce_scan_item(row)
            continue
        items.append(coerce_scan_item(row))
    printed = coerce_totals(raw.get("totals") or raw.get("total") or {})
    if not any(value is not None for value in printed.values()):
        printed = coerce_totals(totals_from_row)
    tables = [coerce_scan_table(row) for row in (raw.get("tables") or [])]
    header = raw.get("header") if isinstance(raw.get("header"), dict) else {}
    return {
        "meaning": raw.get("meaning") or raw.get("document_role"),
        "header": header,
        "tables": tables,
        "totals": printed,
        "items": items,
        "payload": raw,
    }


def _assistant_text(payload: Any) -> str:
    if isinstance(payload, list):
        for item in reversed(payload):
            text = _assistant_text(item)
            if text:
                return text
        return ""
    if not isinstance(payload, dict):
        return str(payload or "")
    chunks: list[str] = []
    for part in payload.get("parts") or []:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        if part_type in {"reasoning", "thinking", "step-start", "step-finish"}:
            continue
        if part.get("text"):
            chunks.append(str(part["text"]))
    if chunks:
        return "\n".join(chunks).strip()
    info = payload.get("info") or {}
    if isinstance(info, dict) and info.get("error"):
        return str(info["error"])
    inner = payload.get("message") or payload.get("data")
    if inner and inner is not payload:
        nested = _assistant_text(inner)
        if nested:
            return nested
    return ""


def _binary_file_part(path: Path) -> dict[str, Any]:
    mime = EXCEL_MIME.get(path.suffix.lower()) or "application/octet-stream"
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "file",
        "mime": mime,
        "filename": path.name,
        "url": f"data:{mime};base64,{b64}",
    }


def _file_part(page: VisionPage) -> dict[str, Any]:
    data = page.path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "file",
        "mime": "image/jpeg",
        "filename": page.path.name,
        "url": f"data:image/jpeg;base64,{b64}",
    }


def review_with_opencode(
    *,
    snapshot: dict[str, Any],
    pages: list[VisionPage] | None = None,
    image_paths: list[Path] | None = None,
    excel_paths: list[Path] | None = None,
) -> dict[str, Any]:
    cfg = settings()
    model_name = cfg["model"]
    vision_pages = list(pages or [])
    if not vision_pages and image_paths:
        vision_pages = [VisionPage(path=path, source_name=path.name, page=None) for path in image_paths]
    excel_files = collect_excel_attachments(excel_paths)
    snapshot["excel_attachments"] = [path.name for path in excel_files]
    result: dict[str, Any] = {
        "status": "skipped",
        "model": model_name,
        "raw_text": "",
        "payload": None,
        "error": None,
        "image_count": len(vision_pages),
        "excel_attached": bool(excel_files),
        "excel_files": [path.name for path in excel_files],
        "meaning": None,
        "header": {},
        "tables": [],
        "totals": None,
        "items": [],
        "context": snapshot.get("context") or {"excel": [], "pdfs": []},
        "model_label": _model_label(model_name),
        "review_cost_usd": None,
        "usage_usd": None,
        "remaining_usd": None,
    }
    if not cfg["enabled"]:
        result["error"] = "OpenCode отключён (OPENCODE_ENABLED=0)"
        return result
    before_billing = fetch_openrouter_billing() if cfg["provider_id"] == "openrouter" else _empty_billing()

    prompt = build_user_prompt(snapshot)
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    # An xlsx sent as a file is rejected ("must be a valid PDF") and the
    # provider then drops the page images too. Cell text from the workbook
    # is already in the prompt. Images are extra pages of the same sheets.
    for page in vision_pages:
        parts.append(_file_part(page))

    auth = None
    if cfg["password"]:
        auth = (cfg["username"], cfg["password"])
    timeout = httpx.Timeout(cfg["timeout_s"], connect=8.0)
    session_id: str | None = None
    try:
        with httpx.Client(base_url=cfg["url"], auth=auth, timeout=timeout) as client:
            created = client.post("/session", json={"title": snapshot.get("title") or "shipment"})
            created.raise_for_status()
            body = created.json()
            session_id = body.get("id") or body.get("sessionID") or (body.get("info") or {}).get("id")
            if not session_id:
                raise RuntimeError(f"нет id сессии: {body}")
            encoded_id = quote(str(session_id), safe="")
            reply = client.post(
                f"/session/{encoded_id}/message",
                json={
                    "system": SYSTEM_PROMPT,
                    "model": {"providerID": cfg["provider_id"], "modelID": cfg["model_id"]},
                    "tools": DISABLED_TOOLS,
                    "parts": parts,
                },
            )
            reply.raise_for_status()
            raw_text = _assistant_text(reply.json())
            result["raw_text"] = raw_text
            if "APIError" in raw_text or "invalid_parameter_error" in raw_text or "invalid_request_error" in raw_text:
                result["status"] = "error"
                result["error"] = raw_text[:500]
                return result
            extracted = extract_json_payload(raw_text)
            result.update(normalize_model_payload(extracted))
            result["status"] = "ok"
            return result
    except httpx.HTTPStatusError as exc:
        result["status"] = "error"
        result["error"] = humanize_exception(exc)
        if not result["raw_text"]:
            result["raw_text"] = result["error"]
        return result
    except Exception as exc:
        result["status"] = "error"
        result["error"] = humanize_exception(exc)
        if not result["raw_text"]:
            result["raw_text"] = result["error"]
        return result
    finally:
        if cfg["provider_id"] == "openrouter":
            after = fetch_openrouter_billing()
            cost = _usage_delta(before_billing, after)
            if result.get("status") == "ok" and cost == 0:
                time_mod.sleep(0.8)
                after = fetch_openrouter_billing()
                cost = _usage_delta(before_billing, after)
            result["usage_usd"] = after.get("usage_usd")
            result["remaining_usd"] = after.get("remaining_usd")
            result["purchased_usd"] = after.get("purchased_usd")
            result["remaining_is_key_limit"] = after.get("remaining_is_key_limit")
            result["review_cost_usd"] = cost
            result["model_label"] = _model_label(model_name)
        if session_id:
            try:
                with httpx.Client(base_url=cfg["url"], auth=auth, timeout=8.0) as client:
                    client.delete(f"/session/{quote(str(session_id), safe='')}")
            except Exception:
                pass
