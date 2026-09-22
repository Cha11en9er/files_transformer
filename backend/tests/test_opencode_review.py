from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.enums import ErrorType
from app.parsing.schemas import ParsedDocument, ParsedLine
from app.schemas.api import ItemOut
from app.services.opencode_review import (
    build_user_prompt,
    compact_parser_snapshot,
    extract_json_payload,
    fetch_openrouter_billing,
    normalize_model_payload,
    ping_opencode,
    probe_opencode,
    _extract_openrouter_key,
    _model_label,
    _usage_delta,
)
from app.services.pdf_pages import VisionPage
from app.services.scan_reconcile import apply_scan_review, compute_excel_totals


def test_extract_json_strips_fences_and_preamble() -> None:
    raw = """Sure.

```json
{"header": {"buyer": "ACME"}, "items": [{"article": "MD 812", "verdict": "ok"}]}
```
"""
    payload = extract_json_payload(raw)
    assert payload["header"]["buyer"] == "ACME"
    assert payload["items"][0]["article"] == "MD 812"


def test_extract_json_from_bare_object() -> None:
    payload = extract_json_payload('{"items":[]}')
    assert payload == {"items": []}


def test_compact_snapshot_keeps_qty_and_excel_context() -> None:
    snap = compact_parser_snapshot(
        title="18018",
        profile_type="BEIJING",
        files=[{"filename": "scan.pdf", "doc_type": "INVOICE", "parse_status": "ok"}],
        header_fields={"buyer": "SM", "empty": ""},
        items=[
            {
                "article": "MD 812",
                "commercial_data": {"price": 0.0592, "amount": 11845.92, "qty": 200100},
                "packing_data": {"net_weight": 1.2, "rolls": None},
                "customs_data": {"hs_code": "7318", "description_ru": "заклепка"},
                "validation_errors": [{"message": "нет в справочнике", "resolved": False}],
            }
        ],
        parsed_docs=[
            ParsedDocument(
                filename="поступление.xlsx",
                file_path="x.xlsx",
                mime_hint="excel",
                sheets=["INV"],
                text_preview="Item No Qty Amount\nMD 812 200100 11845.92",
                lines=[
                    ParsedLine(
                        row_index=2,
                        sheet_name="INV",
                        article="MD 812",
                        raw={"Item No": "MD 812", "Qty": 200100, "Amount": 11845.92},
                    )
                ],
            ),
            ParsedDocument(
                filename="подписанный скан.pdf",
                file_path="scan.pdf",
                mime_hint="pdf",
                text_preview="Invoice 250L Qty 41600 Amount 123.00",
                lines=[
                    ParsedLine(
                        row_index=1,
                        sheet_name="pdf",
                        article="250L",
                        raw={"Item": "250L", "Qty": 41600, "Amount": 123.00},
                    )
                ],
            ),
        ],
        pages=[VisionPage(path=Path("scan_p1.jpg"), source_name="подписанный скан.pdf", page=1)],
        excel_totals={"qty": 200100, "amount": 11845.92},
    )
    row = snap["items"][0]
    assert row["article"] == "MD 812"
    assert row["price"] == 0.0592
    assert row["qty"] == 200100
    assert row["meters"] is None
    assert row["hs_code"] == "7318"
    assert row["flags"] == ["нет в справочнике"]
    assert "empty" not in snap["header"]
    assert snap["excel_totals"]["amount"] == 11845.92
    assert snap["source_files"][0]["filename"] == "поступление.xlsx"
    assert snap["source_files"][0]["table"][0]["qty"] == 200100
    assert snap["context"]["excel"][0]["filename"] == "поступление.xlsx"
    assert "200100" in snap["context"]["excel"][0]["text"]
    assert snap["context"]["excel"][0]["table"][0]["qty"] == 200100
    assert snap["context"]["pdfs"][0]["filename"] == "подписанный скан.pdf"
    assert "41600" in snap["context"]["pdfs"][0]["text"]
    assert snap["context"]["pdfs"][0]["table"][0]["qty"] == 41600
    assert snap["context"]["pdfs"][0]["pages"] == [1]
    assert snap["images_manifest"][0]["source"] == "подписанный скан.pdf"
    prompt = build_user_prompt(snap)
    assert "source_files" in prompt
    assert "excel_totals" in prompt
    assert "ignored" in prompt
    assert "Müşteri Kodu" in prompt
    assert "ZIMMY" in prompt
    assert "DYER 789" in prompt
    assert "document_shapes" in prompt
    assert "SOFA FABRIC" in prompt
    assert "PACKAGES" in prompt
    assert "excel_attachments" in snap
    assert "Invoice + Packing list" in prompt
    assert "HIDES" in prompt
    assert "languages_in_this_shipment" in prompt
    assert "invoice_date" in prompt
    assert "shipment" in prompt.lower()
    assert "lots[]" in prompt
    assert "KEEP BOTH" not in prompt
    assert "letterhead" in prompt.lower() or "Cross-supplier principles" in prompt
    assert "catalog" in prompt.lower() or "сводная" in prompt
    assert "Later pages" in prompt
    assert "NO Excel" in prompt
    assert "54.07.73" in prompt or "54.07.73.00.90.11" in prompt
    assert "dirham" in prompt.lower() or "GBP" in prompt
    assert "UNIT PICE" in prompt or "METRS" in prompt
    assert "Customer Name" in prompt
    assert "SERIES / ART." in prompt
    assert "parser_json.items is empty" in prompt
    assert "goods row is structural" in prompt
    assert snap["languages"]["ids"]
    assert "en" in snap["languages"]["ids"]
    assert snap["source_files"][0]["scripts"]


def test_snapshot_serializes_excel_datetime() -> None:
    from datetime import datetime

    snap = compact_parser_snapshot(
        title="18312",
        profile_type="18233",
        files=[{"filename": "invoice.xlsx", "doc_type": "INVOICE", "parse_status": "ok"}],
        header_fields={"date": datetime(2026, 3, 15, 0, 0, 0)},
        items=[],
        parsed_docs=[
            ParsedDocument(
                filename="invoice.xlsx",
                file_path="invoice.xlsx",
                mime_hint="excel",
                sheets=["INV"],
                lines=[
                    ParsedLine(
                        row_index=2,
                        sheet_name="INV",
                        article="EL-1",
                        raw={"Date": datetime(2026, 3, 15), "Art": "EL-1"},
                    )
                ],
            )
        ],
    )
    assert isinstance(snap["header"]["date"], str)
    assert "2026-03-15" in snap["header"]["date"]
    assert isinstance(snap["context"]["excel"][0]["table"][0]["raw"]["Date"], str)
    prompt = build_user_prompt(snap)
    assert "EL-1" in prompt


def test_normalize_payload_lifts_total_row_and_tables() -> None:
    normalized = normalize_model_payload(
        {
            "meaning": "signed invoice",
            "tables": [
                {"role": "goods", "page": "1", "why": "articles", "columns": {"qty": "Quantity"}},
                {"role": "bank", "why": "beneficiary"},
            ],
            "items": [
                {"article": "250L", "qty": "41600", "amount": "123"},
                {"article": "Total", "qty": "416000", "amount": "510259.00"},
            ],
        }
    )
    assert normalized["meaning"] == "signed invoice"
    assert len(normalized["items"]) == 1
    assert normalized["items"][0]["qty"] == 41600.0
    assert normalized["totals"]["qty"] == 416000.0
    assert normalized["totals"]["amount"] == 510259.0
    assert normalized["tables"][0]["role"] == "goods"
    assert normalized["tables"][0]["page"] == 1
    assert normalized["tables"][1]["role"] == "ignored"


def test_apply_scan_flags_mismatch_and_keeps_excel_values() -> None:
    item = ItemOut(
        id=uuid4(),
        article="250L",
        model="250L",
        normalized_article="250L",
        commercial_data={"qty": 100, "amount": 10},
        packing_data={},
        customs_data={},
    )
    review = apply_scan_review(
        [item],
        {
            "status": "ok",
            "totals": {"qty": 416000, "amount": 510259.0},
            "items": [{"article": "250L", "qty": 41600, "amount": 123.0, "verdict": "question"}],
        },
    )
    assert item.commercial_data["qty"] == 100
    assert any(err.error_type == ErrorType.SCAN_MISMATCH for err in item.validation_errors)
    assert review["items"][0]["item_id"] == str(item.id)
    assert review["items"][0]["verdict"] == "question"
    assert review["excel_totals"]["qty"] == 100
    assert review["totals_mismatch"] is True


def test_apply_scan_does_not_mark_missing_when_no_article_overlap() -> None:
    item = ItemOut(
        id=uuid4(),
        article="MD 812",
        model=None,
        normalized_article="MD812",
        commercial_data={"qty": 200100, "amount": 1},
        packing_data={},
        customs_data={},
    )
    review = apply_scan_review(
        [item],
        {
            "status": "ok",
            "totals": {"qty": 416000, "amount": 510259.0},
            "items": [{"article": "250L", "qty": 41600, "amount": 123.0}],
        },
    )
    assert not any(err.error_type == ErrorType.MISSING_PAIR for err in item.validation_errors)
    assert review["items"][0]["verdict"] == "extra"


def test_compute_excel_totals_sums_qty() -> None:
    items = [
        ItemOut(
            id=uuid4(),
            article="A",
            model=None,
            normalized_article="A",
            commercial_data={"qty": 10, "amount": 2.5},
            packing_data={"meters": None},
            customs_data={},
        ),
        ItemOut(
            id=uuid4(),
            article="B",
            model=None,
            normalized_article="B",
            commercial_data={"qty": 20, "amount": 7.5},
            packing_data={},
            customs_data={},
        ),
    ]
    totals = compute_excel_totals(items)
    assert totals.qty == 30
    assert totals.amount == 10.0


class _FakeReply:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, reply: _FakeReply | Exception) -> None:
        self._reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path: str, **kwargs):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def test_probe_opencode_off(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_ENABLED", "0")
    result = probe_opencode()
    assert result["status"] == "off"
    assert "выключена" in result["title"].lower() or "выключена" in result["detail"].lower()


def test_probe_opencode_ok(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_ENABLED", "1")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "secret")
    fake = _FakeClient(_FakeReply(200, "ok"))
    with patch("app.services.opencode_review.httpx.Client", return_value=fake):
        result = probe_opencode()
    assert result["status"] == "ok"
    assert "жива" in result["title"].lower()


def test_probe_opencode_auth(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_ENABLED", "1")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "wrong")
    fake = _FakeClient(_FakeReply(401, "Unauthorized"))
    with patch("app.services.opencode_review.httpx.Client", return_value=fake):
        result = probe_opencode()
    assert result["status"] == "auth"
    assert "пароль" in result["title"].lower() or "пароль" in result["detail"].lower()


def test_probe_opencode_missing_password(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_ENABLED", "1")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "")
    fake = _FakeClient(_FakeReply(401, "Unauthorized"))
    with patch("app.services.opencode_review.httpx.Client", return_value=fake):
        result = probe_opencode()
    assert result["status"] == "auth"
    assert "нет пароля" in result["title"].lower()


def test_probe_opencode_down(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_ENABLED", "1")
    fake = _FakeClient(ConnectionError("Connection refused"))
    with patch("app.services.opencode_review.httpx.Client", return_value=fake):
        result = probe_opencode()
    assert result["status"] == "down"
    assert "10061" not in result["detail"]
    assert "Connection refused" not in result["detail"]


def test_ping_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_ENABLED", "0")
    result = ping_opencode()
    assert result["status"] == "off"
    assert result["reply"] is None


def test_model_label_qwen38_max() -> None:
    assert _model_label("openrouter/qwen/qwen3.8-max-0902") == "Qwen3.8 Max 0902"


def test_extract_openrouter_key_from_auth() -> None:
    blob = {"openrouter": {"type": "api", "key": "sk-or-v1-secret-example"}}
    assert _extract_openrouter_key(blob) == "sk-or-v1-secret-example"


def test_usage_delta() -> None:
    assert _usage_delta({"usage_usd": 1.0}, {"usage_usd": 1.37}) == 0.37


class _JsonReply:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _BillingClient:
    def __init__(self, key_payload: dict, credits_status: int = 200, credits_payload: dict | None = None) -> None:
        self.key_payload = key_payload
        self.credits_status = credits_status
        self.credits_payload = credits_payload or {"data": {"total_credits": 10.0, "total_usage": 1.25}}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str, **kwargs):
        path = str(url)
        if path.endswith("/key"):
            return _JsonReply(200, {"data": self.key_payload})
        if "credits" in path:
            return _JsonReply(self.credits_status, self.credits_payload)
        return _JsonReply(404, {})


def test_openrouter_billing_account_remaining(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_MODEL", "openrouter/qwen/qwen3.8-max-0902")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")
    fake = _BillingClient({"usage": 1.25, "limit_remaining": 98.75, "limit": 100})
    with patch("app.services.opencode_review.httpx.Client", return_value=fake):
        billing = fetch_openrouter_billing()
    assert billing["usage_usd"] == 1.25
    assert billing["remaining_usd"] == 8.75
    assert billing["remaining_is_key_limit"] is False


def test_openrouter_billing_falls_back_to_key_limit(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_MODEL", "openrouter/qwen/qwen3.8-max-0902")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")
    fake = _BillingClient(
        {"usage": 1.25, "limit_remaining": 98.75, "limit": 100},
        credits_status=403,
        credits_payload={"error": {"message": "Only management keys"}},
    )
    with patch("app.services.opencode_review.httpx.Client", return_value=fake):
        billing = fetch_openrouter_billing()
    assert billing["usage_usd"] == 1.25
    assert billing["remaining_usd"] == 98.75
    assert billing["remaining_is_key_limit"] is True


class _HealthAndBillingClient:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path: str, **kwargs):
        url = str(path)
        if url in {"/global/health", "/session", "/doc", "/"}:
            return _FakeReply(200, "ok")
        if url.endswith("/key"):
            return _JsonReply(200, {"data": {"usage": 0.4, "limit_remaining": 99.6, "limit": 100}})
        if "credits" in url:
            return _JsonReply(200, {"data": {"total_credits": 10.0, "total_usage": 0.4}})
        return _FakeReply(404, "")


def test_probe_shows_openrouter_balance(monkeypatch) -> None:
    monkeypatch.setenv("OPENCODE_ENABLED", "1")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "secret")
    monkeypatch.setenv("OPENCODE_MODEL", "openrouter/qwen/qwen3.8-max-0902")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")
    with patch("app.services.opencode_review.httpx.Client", return_value=_HealthAndBillingClient()):
        result = probe_opencode()
    assert result["status"] == "ok"
    assert "Qwen3.8 Max 0902" in result["title"]
    assert result["remaining_usd"] == 9.6
    assert "остаток $9.60" in result["title"]


def test_as_float_keeps_european_comma() -> None:
    from app.services.opencode_review import _as_float

    assert _as_float("10,4") == 10.4
    assert _as_float("19 624,80") == 19624.80
    assert _as_float("1,234.56") == 1234.56


def test_collect_excel_attachments_skips_catalog(tmp_path: Path) -> None:
    from app.services.opencode_review import collect_excel_attachments

    invoice = tmp_path / "invoice.xlsx"
    catalog = tmp_path / "справочник сводная.xlsx"
    invoice.write_bytes(b"PK\x03\x04tiny")
    catalog.write_bytes(b"PK\x03\x04catalog")
    found = collect_excel_attachments([invoice, catalog])
    assert [p.name for p in found] == ["invoice.xlsx"]


def test_apply_excel_attached_corrects_amount() -> None:
    item = ItemOut(
        id=uuid4(),
        article="Sherlock 980",
        model="Sherlock 980",
        normalized_article="SHERLOCK980",
        commercial_data={"qty": 1887, "price": 10.4, "amount": 2736.15},
        packing_data={"area": 2736.15, "meters": 1887},
        customs_data={},
    )
    apply_scan_review(
        [item],
        {
            "status": "ok",
            "excel_attached": True,
            "items": [
                {
                    "article": "Sherlock 980",
                    "amount": 19624.8,
                    "price": 10.4,
                    "verdict": "question",
                    "notes": "excel: amount from AMOUNT(RMB)",
                }
            ],
        },
    )
    assert abs(float(item.commercial_data["amount"]) - 19624.8) < 0.05
    assert any(err.error_type == ErrorType.MODEL_CORRECTION for err in item.validation_errors)


def test_apply_excel_attached_appends_missing_article() -> None:
    item = ItemOut(
        id=uuid4(),
        article="KD020",
        model="KD020",
        normalized_article="KD020",
        commercial_data={"qty": 5040, "price": 3.35, "amount": 16884},
        packing_data={},
        customs_data={},
    )
    items = [item]
    apply_scan_review(
        items,
        {
            "status": "ok",
            "excel_attached": True,
            "items": [
                {
                    "article": "767B",
                    "qty": 3000,
                    "price": 16.254,
                    "amount": 48762,
                    "verdict": "extra",
                    "notes": "excel: missing from draft",
                }
            ],
        },
    )
    assert len(items) == 2
    added = next(row for row in items if row.article == "767B")
    assert added.commercial_data["qty"] == 3000
    assert any(err.error_type == ErrorType.MODEL_CORRECTION for err in added.validation_errors)


def test_apply_scan_review_keeps_extra_without_sku():
    items: list[ItemOut] = []
    apply_scan_review(
        items,
        {
            "status": "ok",
            "excel_attached": False,
            "items": [
                {
                    "article": "-",
                    "description": "BALLOONS / ВОЗДУШНЫЙ ШАР",
                    "qty": 2800,
                    "amount": 1972.04,
                    "verdict": "extra",
                }
            ],
        },
    )
    assert len(items) == 1
    assert "BALLOON" in (items[0].article or "").upper() or "ШАР" in (items[0].customs_data.get("description") or "").upper()
    assert items[0].commercial_data["qty"] == 2800


def test_coerce_brutto_alias_and_extra_lot_of_same_article() -> None:
    from app.services.opencode_review import coerce_scan_item, normalize_model_payload

    item = coerce_scan_item({"article": "WAY04", "qty": 10, "brutto": 17288.94, "netto": 16000, "packages": 40})
    assert item["gross_weight"] == 17288.94
    assert item["net_weight"] == 16000
    assert item["rolls"] == 40

    payload = normalize_model_payload(
        {"totals": {"weight brutto": 100, "qty": 5}, "items": [{"article": "A", "qty": 1, "verdict": "ok"}]}
    )
    assert payload["totals"]["gross_weight"] == 100

    existing = ItemOut(
        id=uuid4(),
        article="NO228",
        model="NO228",
        normalized_article="NO228",
        commercial_data={"qty": 1480, "amount": 100},
        packing_data={"gross_weight": 10},
        customs_data={},
    )
    items = [existing]
    apply_scan_review(
        items,
        {
            "status": "ok",
            "excel_attached": True,
            "items": [
                {
                    "article": "NO228",
                    "qty": 20,
                    "amount": 24,
                    "gross_weight": 6,
                    "verdict": "extra",
                    "notes": "excel: second lot",
                }
            ],
        },
    )
    assert len(items) == 2
    assert sorted(row.commercial_data["qty"] for row in items) == [20, 1480]


def test_apply_scan_pdf_only_appends_continuation_article() -> None:
    existing = ItemOut(
        id=uuid4(),
        article="31021",
        model="31021",
        normalized_article="31021",
        commercial_data={"qty": 360, "amount": 46.94},
        packing_data={},
        customs_data={},
    )
    items = [existing]
    apply_scan_review(
        items,
        {
            "status": "ok",
            "excel_attached": False,
            "items": [
                {
                    "article": "67004",
                    "qty": 262,
                    "amount": 2480.04,
                    "verdict": "extra",
                    "notes": "page 2 continuation",
                }
            ],
        },
    )
    assert len(items) == 2
    added = next(row for row in items if row.article == "67004")
    assert added.commercial_data["qty"] == 262

