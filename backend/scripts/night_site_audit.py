"""Ночной прогон 10 поставок на боевом :8200. Пишет roadmap/night_audit_10.md.

  python backend/scripts/night_site_audit.py
  python backend/scripts/night_site_audit.py --base-url http://87.251.86.53:8200 --limit 10
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_MD = ROOT / "roadmap" / "night_audit_10.md"
OUT_DIR = ROOT / "backend" / "scripts" / "debug_docs" / "out" / "night_site_audit"
ACCEPT_RE = re.compile(r"\.(xlsx|xls|xlsm|pdf|png|jpe?g|tif{1,2}|bmp|webp)$", re.I)
SKIP_RE = re.compile(r"(проверк|etalon|эталон|комм\.?txt|thumbs\.db|\.ds_store|~\$)", re.I)
CATALOG_RE = re.compile(r"(справочник|сводная|catalog|catalogue|описание)", re.I)

# 5 из documents/10_pravka + 5 Excel/контроль из я_тестирую.
KITS: list[dict[str, Any]] = [
    {
        "id": "10_pravka/Светильники",
        "root": ROOT / "documents" / "10_pravka" / "Светильники",
        "profile": "18233",
        "why": "Excel INV+PL+SPEC ~25 товаров (то, что крутилось 12+ мин)",
        "prefer_excel": True,
    },
    {
        "id": "10_pravka/Генератор",
        "root": ROOT / "documents" / "10_pravka" / "Генератор",
        "profile": "18233",
        "why": "Excel + PDF той же поставки",
        "prefer_excel": True,
    },
    {
        "id": "10_pravka/Датчик",
        "root": ROOT / "documents" / "10_pravka" / "Датчик",
        "profile": "BEIJING",
        "why": "PDF + эталон ТСД",
    },
    {
        "id": "10_pravka/Грибы",
        "root": ROOT / "documents" / "10_pravka" / "Грибы",
        "profile": "BEIJING",
        "why": "PDF + TERMS xlsx",
    },
    {
        "id": "10_pravka/продукты питания",
        "root": ROOT / "documents" / "10_pravka" / "продукты питания",
        "profile": "BEIJING",
        "why": "PDF спецификация + ТСД",
    },
    {
        "id": "я_тестирую/01_ханчжоу_18233_626-1",
        "root": ROOT / "documents" / "я_тестирую" / "01_ханчжоу_18233_626-1",
        "profile": "18233",
        "why": "Контроль Hangzhou 18233",
    },
    {
        "id": "я_тестирую/11_beijing_goldluck_003",
        "root": ROOT / "documents" / "я_тестирую" / "11_beijing_goldluck_003",
        "profile": "BEIJING",
        "why": "Контроль Beijing",
    },
    {
        "id": "я_тестирую/12_beijing_goldluck_002",
        "root": ROOT / "documents" / "я_тестирую" / "12_beijing_goldluck_002",
        "profile": "BEIJING",
        "why": "Второй Beijing",
    },
    {
        "id": "я_тестирую/02_ханчжоу_18233_626-2",
        "root": ROOT / "documents" / "я_тестирую" / "02_ханчжоу_18233_626-2",
        "profile": "18233",
        "why": "Hangzhou вторая половина",
    },
    {
        "id": "я_тестирую/13_bestway_way04",
        "root": ROOT / "documents" / "я_тестирую" / "13_bestway_way04",
        "profile": "18233",
        "why": "PDF PACKAGE≠QTY",
    },
]


def _list_files(kit: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    root: Path = kit["root"]
    goods: list[Path] = []
    catalog: list[Path] = []
    if not root.is_dir():
        return goods, catalog
    prefer_excel = bool(kit.get("prefer_excel"))
    excel_only = prefer_excel
    candidates: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if SKIP_RE.search(path.name):
            continue
        if "Один эксель" in path.parts:
            continue
        if "Версия после" in " ".join(path.parts):
            continue
        if not ACCEPT_RE.search(path.name):
            continue
        candidates.append(path)
    if excel_only:
        excel = [p for p in candidates if p.suffix.lower() in {".xlsx", ".xls", ".xlsm"}]
        if len(excel) >= 2:
            candidates = excel
    for path in candidates:
        if CATALOG_RE.search(path.name):
            catalog.append(path)
        else:
            goods.append(path)
    return goods, catalog


def _parse_stream(text: str) -> dict[str, Any]:
    done: dict[str, Any] | None = None
    stages: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("event") == "progress":
            stages.append(
                {
                    "stage": event.get("stage"),
                    "message": event.get("message"),
                    "detail": event.get("detail"),
                    "model_phase": event.get("model_phase"),
                    "fast_verdict": event.get("fast_verdict"),
                    "disk_count": event.get("disk_count"),
                    "route": event.get("route"),
                }
            )
        elif event.get("event") == "done":
            done = {k: v for k, v in event.items() if k != "event"}
        elif event.get("event") == "error":
            raise RuntimeError(event.get("detail") or "server error")
    if done is None and text.strip().startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "items" in data:
                done = data
        except json.JSONDecodeError:
            pass
    if done is None:
        raise RuntimeError("нет события done в ответе")
    done["_stages"] = stages
    return done


def _append_md(block: str) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with OUT_MD.open("a", encoding="utf-8") as fh:
        fh.write(block.rstrip() + "\n\n")


def _zip_names(content: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            return zf.namelist()
    except Exception:
        return []


def run_one(client: httpx.Client, base_url: str, kit: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    goods, catalog = _list_files(kit)
    result: dict[str, Any] = {
        "id": kit["id"],
        "profile": kit["profile"],
        "why": kit.get("why"),
        "files": [p.name for p in goods],
        "catalog": [p.name for p in catalog],
        "status": "started",
    }
    if not goods:
        result["status"] = "skip"
        result["error"] = "нет входных файлов"
        return result

    files = []
    for path in goods:
        files.append(("files", (path.name, path.read_bytes(), "application/octet-stream")))
    for path in catalog:
        files.append(("extra_files", (path.name, path.read_bytes(), "application/octet-stream")))

    t0 = time.time()
    # timeout=None: ждём модель сколько нужно (как OPENCODE_TIMEOUT_S=0 на сервере).
    with client.stream(
        "POST",
        f"{base_url.rstrip('/')}/api/v1/shipments/",
        data={
            "title": f"night-{kit['id'].replace('/', '-')}",
            "profile_type": kit["profile"],
        },
        files=files,
        timeout=None,
    ) as resp:
        chunks: list[str] = []
        for chunk in resp.iter_text():
            chunks.append(chunk)
        body = "".join(chunks)
        resp.raise_for_status()
    elapsed = time.time() - t0
    (run_dir / "api_raw.txt").write_text(body[:500_000], encoding="utf-8")
    done = _parse_stream(body)
    (run_dir / "api_done.json").write_text(
        json.dumps({k: v for k, v in done.items() if k != "_stages"}, ensure_ascii=False, indent=2, default=str)[:1_500_000],
        encoding="utf-8",
    )

    items = done.get("items") or []
    header = done.get("header_fields") or {}
    review = done.get("model_review") or {}
    recognition = done.get("recognition") or {}
    result.update(
        {
            "status": "ok",
            "elapsed_sec": round(elapsed, 1),
            "item_count": len(items),
            "warning_count": done.get("warning_count"),
            "route": recognition.get("route") or done.get("route"),
            "invoice_no": header.get("invoice_no"),
            "seller": (header.get("seller") or "")[:80],
            "buyer": (header.get("buyer") or "")[:80],
            "model_status": review.get("status"),
            "model_meaning": (review.get("meaning") or "")[:300],
            "model_error": review.get("error"),
            "model_label": review.get("model_label") or review.get("model"),
            "stages": done.get("_stages") or [],
            "fast_seen": any(s.get("fast_verdict") for s in (done.get("_stages") or [])),
        }
    )

    # Выгрузка
    try:
        export = client.post(
            f"{base_url.rstrip('/')}/api/v1/shipments/export",
            json={
                "title": done.get("title") or kit["id"],
                "profile_type": kit["profile"],
                "header_fields": header,
                "items": items,
            },
            timeout=180.0,
        )
        export.raise_for_status()
        export_path = run_dir / "export.bin"
        export_path.write_bytes(export.content)
        names = _zip_names(export.content)
        if not names and export.content[:2] == b"PK":
            names = ["(xlsx workbook)"]
        result["export_ok"] = True
        result["export_files"] = names
        result["export_bytes"] = len(export.content)
    except Exception as exc:  # noqa: BLE001
        result["export_ok"] = False
        result["export_error"] = str(exc)

    # Короткая сводка склейки
    sample = []
    for item in items[:5]:
        c = item.get("commercial_data") or {}
        p = item.get("packing_data") or {}
        sample.append(
            {
                "article": c.get("article") or c.get("vendor") or item.get("article"),
                "qty": c.get("qty"),
                "packages": p.get("rolls") or p.get("boxes") or c.get("packages"),
                "amount": c.get("amount"),
                "net": p.get("net_weight"),
            }
        )
    result["sample_rows"] = sample
    return result


def format_result(result: dict[str, Any]) -> str:
    lines = [
        f"## {result.get('id')}",
        f"- profile: `{result.get('profile')}`",
        f"- why: {result.get('why')}",
        f"- status: **{result.get('status')}**",
        f"- elapsed: {result.get('elapsed_sec')}s",
        f"- files: {result.get('files')}",
        f"- items: {result.get('item_count')}, warnings: {result.get('warning_count')}",
        f"- route: {result.get('route')}, fast_seen: {result.get('fast_seen')}",
        f"- invoice: {result.get('invoice_no')}",
        f"- seller: {result.get('seller')}",
        f"- buyer: {result.get('buyer')}",
        f"- model: {result.get('model_status')} / {result.get('model_label')}",
        f"- meaning: {result.get('model_meaning')}",
        f"- model_error: {result.get('model_error')}",
        f"- export_ok: {result.get('export_ok')}, files: {result.get('export_files')}",
        f"- sample: `{json.dumps(result.get('sample_rows'), ensure_ascii=False)}`",
    ]
    if result.get("error"):
        lines.append(f"- error: `{result.get('error')}`")
    if result.get("export_error"):
        lines.append(f"- export_error: `{result.get('export_error')}`")
    stages = result.get("stages") or []
    if stages:
        lines.append("- stages:")
        for stage in stages[-12:]:
            lines.append(
                f"  - {stage.get('stage')}/{stage.get('model_phase') or '-'}: {stage.get('message')}"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://87.251.86.53:8200")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()

    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_dir = OUT_DIR / session
    session_dir.mkdir(parents=True, exist_ok=True)

    header = "\n".join(
        [
            f"# Ночной аудит 10 поставок — {session}",
            "",
            f"- base: `{args.base_url}`",
            f"- out: `{session_dir}`",
            "- цель: склейка, выгрузка, тайминги, где тормозит модель",
            "",
            "## Скорость (ожидаемые правки в коде)",
            "- known+Excel → FAST_RULES, qwen/OPENCODE_MODEL_FAST, без второй модели",
            "- OPENCODE_TIMEOUT_S=0 — ждать ответ, не обрывать",
            "- если на сервере ещё старый билд — elapsed будет 10-25 мин на Excel",
            "",
        ]
    )
    if not OUT_MD.exists() or OUT_MD.stat().st_size == 0:
        OUT_MD.write_text(header, encoding="utf-8")
    else:
        _append_md("---\n\n" + header)

    kits = KITS[args.start : args.start + args.limit]
    ok = 0
    fail = 0
    with httpx.Client(timeout=None) as client:
        # health
        try:
            health = client.get(f"{args.base_url.rstrip('/')}/", timeout=30.0)
            _append_md(f"Сайт отвечает: HTTP {health.status_code}")
        except Exception as exc:  # noqa: BLE001
            _append_md(f"Сайт недоступен: `{exc}`")
            return 2

        for kit in kits:
            kit_dir = session_dir / re.sub(r"[^\w\-]+", "_", kit["id"], flags=re.U)
            kit_dir.mkdir(parents=True, exist_ok=True)
            print(f"=== {kit['id']} ===", flush=True)
            try:
                result = run_one(client, args.base_url, kit, kit_dir)
                if result.get("status") == "ok":
                    ok += 1
                else:
                    fail += 1
            except Exception as exc:  # noqa: BLE001
                fail += 1
                result = {
                    "id": kit["id"],
                    "profile": kit["profile"],
                    "why": kit.get("why"),
                    "status": "failed",
                    "error": str(exc),
                    "files": [p.name for p in _list_files(kit)[0]],
                }
                print(f"FAIL {kit['id']}: {exc}", flush=True)
            (kit_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            _append_md(format_result(result))
            print(
                f"-> {result.get('status')} items={result.get('item_count')} "
                f"elapsed={result.get('elapsed_sec')}s export={result.get('export_ok')}",
                flush=True,
            )

    _append_md(f"## Итог сессии\n- ok: {ok}\n- fail/skip: {fail}\n- session: `{session}`\n")
    print(f"DONE ok={ok} fail={fail} md={OUT_MD}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
