"""Прогон поставок на :8200 по одной: загрузка → проверка → запись в md → следующая.

Не берёт documents/10_pravka (последняя правка). По умолчанию 10 случайных из я_тестирую.

  python backend/scripts/night_site_audit.py
  python backend/scripts/night_site_audit.py --seed 42 --limit 10
"""

from __future__ import annotations

import argparse
import json
import random
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
KITS_ROOT = ROOT / "documents" / "я_тестирую"
OUT_MD = ROOT / "roadmap" / "night_audit_10.md"
OUT_DIR = ROOT / "backend" / "scripts" / "debug_docs" / "out" / "night_site_audit"
ACCEPT_RE = re.compile(r"\.(xlsx|xls|xlsm|pdf|png|jpe?g|tif{1,2}|bmp|webp)$", re.I)
SKIP_NAME_RE = re.compile(
    r"(проверк|etalon|эталон|результат|комм\.?txt|thumbs\.db|\.ds_store|~\$|не_вход)",
    re.I,
)
SKIP_DIR_RE = re.compile(r"(эталон|etalon|результат|не_вход|мой)", re.I)
CATALOG_RE = re.compile(r"(справочник|сводная|catalog|catalogue)", re.I)


def _guess_profile(name: str) -> str:
    low = name.lower()
    if "beijing" in low or "goldluck" in low:
        return "BEIJING"
    return "18233"


def discover_kits() -> list[dict[str, Any]]:
    kits: list[dict[str, Any]] = []
    if not KITS_ROOT.is_dir():
        return kits
    for folder in sorted(KITS_ROOT.iterdir()):
        if not folder.is_dir():
            continue
        goods, catalog = list_input_files(folder)
        if not goods:
            continue
        kits.append(
            {
                "id": f"я_тестирую/{folder.name}",
                "root": folder,
                "profile": _guess_profile(folder.name),
                "why": "рандом из я_тестирую (не 10_pravka)",
            }
        )
    return kits


def list_input_files(root: Path) -> tuple[list[Path], list[Path]]:
    goods: list[Path] = []
    catalog: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(SKIP_DIR_RE.search(part) for part in path.parts):
            continue
        if SKIP_NAME_RE.search(path.name):
            continue
        if not ACCEPT_RE.search(path.name):
            continue
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
                    "draft_lots": event.get("draft_lots"),
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
        fh.flush()


def _zip_names(content: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            return zf.namelist()
    except Exception:
        return ["(не zip)"] if content[:2] != b"PK" else ["(xlsx)"]


def _sample_rows(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    sample = []
    for item in items[:limit]:
        c = item.get("commercial_data") or {}
        p = item.get("packing_data") or {}
        sample.append(
            {
                "article": c.get("article") or c.get("vendor") or item.get("article") or "",
                "qty": c.get("qty"),
                "packages": p.get("rolls") or p.get("boxes") or c.get("packages"),
                "amount": c.get("amount"),
                "net": p.get("net_weight"),
                "gross": p.get("gross_weight"),
                "desc": str(c.get("description") or item.get("description") or "")[:80],
            }
        )
    return sample


def run_one(client: httpx.Client, base_url: str, kit: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    goods, catalog = list_input_files(kit["root"])
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

    print(f"  upload {len(goods)} files + {len(catalog)} catalog …", flush=True)
    t0 = time.time()
    last_ping = t0
    chunks: list[str] = []
    with client.stream(
        "POST",
        f"{base_url.rstrip('/')}/api/v1/shipments/",
        data={
            "title": f"audit-{Path(kit['id']).name}",
            "profile_type": kit["profile"],
            "stream": "1",
        },
        files=files,
        timeout=None,
    ) as resp:
        if resp.status_code >= 400:
            body = resp.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {resp.status_code}: {body[:500]}")
        for chunk in resp.iter_text():
            chunks.append(chunk)
            now = time.time()
            if now - last_ping >= 30:
                # partial progress from buffered lines
                buf = "".join(chunks)
                for line in buf.splitlines()[::-1]:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event") == "progress":
                        print(
                            f"  … {int(now - t0)}s · {ev.get('stage')}/{ev.get('model_phase') or '-'} · {ev.get('message')}",
                            flush=True,
                        )
                        break
                last_ping = now
        body = "".join(chunks)
    elapsed = time.time() - t0
    (run_dir / "api_raw.txt").write_text(body[:500_000], encoding="utf-8")
    done = _parse_stream(body)
    (run_dir / "api_done.json").write_text(
        json.dumps({k: v for k, v in done.items() if k != "_stages"}, ensure_ascii=False, indent=2, default=str)[
            :1_500_000
        ],
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
            "contract": header.get("contract"),
            "currency": header.get("currency"),
            "delivery": header.get("delivery"),
            "seller": (header.get("seller") or "")[:120],
            "buyer": (header.get("buyer") or "")[:120],
            "model_status": review.get("status"),
            "model_meaning": (review.get("meaning") or "")[:400],
            "model_error": review.get("error"),
            "model_label": review.get("model_label") or review.get("model"),
            "stages": done.get("_stages") or [],
            "fast_seen": any(s.get("fast_verdict") for s in (done.get("_stages") or [])),
            "sample_rows": _sample_rows(items),
            "header_notes": list(done.get("header_notes") or [])[:8],
        }
    )

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
        (run_dir / "export.bin").write_bytes(export.content)
        result["export_ok"] = True
        result["export_files"] = _zip_names(export.content)
        result["export_bytes"] = len(export.content)
    except Exception as exc:  # noqa: BLE001
        result["export_ok"] = False
        result["export_error"] = str(exc)
    return result


def format_result(index: int, total: int, result: dict[str, Any]) -> str:
    lines = [
        f"## {index}/{total} · {result.get('id')}",
        f"- profile: `{result.get('profile')}`",
        f"- status: **{result.get('status')}** · elapsed: **{result.get('elapsed_sec')}s**",
        f"- files in: {result.get('files')}",
        f"- catalog: {result.get('catalog') or '-'}",
        f"- items: {result.get('item_count')} · warnings: {result.get('warning_count')}",
        f"- route: `{result.get('route')}` · fast_verdict: {result.get('fast_seen')}",
        f"- invoice: `{result.get('invoice_no')}` · contract: `{result.get('contract')}`",
        f"- currency: `{result.get('currency')}` · delivery: `{result.get('delivery')}`",
        f"- seller: {result.get('seller')}",
        f"- buyer: {result.get('buyer')}",
        f"- model: `{result.get('model_status')}` / {result.get('model_label')}",
        f"- meaning: {result.get('model_meaning')}",
        f"- model_error: {result.get('model_error') or '-'}",
        f"- export_ok: {result.get('export_ok')} · files: {result.get('export_files')}",
    ]
    if result.get("header_notes"):
        lines.append("- header_notes:")
        for note in result["header_notes"]:
            lines.append(f"  - {note}")
    if result.get("sample_rows"):
        lines.append("- sample rows (first):")
        lines.append(f"  ```json\n  {json.dumps(result['sample_rows'], ensure_ascii=False, indent=2)}\n  ```")
    stages = result.get("stages") or []
    if stages:
        lines.append("- last stages:")
        for stage in stages[-10:]:
            lines.append(
                f"  - {stage.get('stage')}/{stage.get('model_phase') or '-'}: {stage.get('message')}"
            )
    if result.get("error"):
        lines.append(f"- error: `{result.get('error')}`")
    if result.get("export_error"):
        lines.append(f"- export_error: `{result.get('export_error')}`")
    # короткая оценка «что распозналось»
    verdict = "пусто"
    if result.get("status") == "ok" and (result.get("item_count") or 0) > 0:
        if result.get("model_status") == "ok":
            verdict = "строки есть, модель ответила"
        elif result.get("model_status") in {"error", "skipped"}:
            verdict = f"строки есть (черновик кода), модель: {result.get('model_status')}"
        else:
            verdict = "строки есть"
        if not result.get("export_ok"):
            verdict += "; выгрузка упала"
        if not result.get("invoice_no") and not result.get("seller"):
            verdict += "; шапка почти пустая"
    elif result.get("status") == "ok":
        verdict = "готово, но 0 позиций"
    elif result.get("status") == "failed":
        verdict = "провал"
    lines.append(f"- **оценка:** {verdict}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://87.251.86.53:8200")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--seed", type=int, default=None, help="фиксированный random seed")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else int(time.time()) % 10_000_000
    rng = random.Random(seed)

    all_kits = discover_kits()
    if not all_kits:
        print("нет китов в documents/я_тестирую", flush=True)
        return 2
    pick = list(all_kits)
    rng.shuffle(pick)
    kits = pick[: max(1, args.limit)]

    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_dir = OUT_DIR / session
    session_dir.mkdir(parents=True, exist_ok=True)

    header = "\n".join(
        [
            f"# Аудит 10 поставок — {session}",
            "",
            f"- base: `{args.base_url}`",
            f"- seed: `{seed}` (рандом из я_тестирую, **без** 10_pravka)",
            f"- принцип: загрузка → проверка → запись сюда → следующая",
            f"- out: `{session_dir}`",
            f"- очередь ({len(kits)}):",
        ]
        + [f"  {i+1}. `{k['id']}` · {k['profile']}" for i, k in enumerate(kits)]
        + ["", ""]
    )
    _append_md("---\n\n" + header)
    print(header, flush=True)

    ok = 0
    fail = 0
    with httpx.Client(timeout=None) as client:
        try:
            health = client.get(f"{args.base_url.rstrip('/')}/", timeout=30.0)
            _append_md(f"Сайт отвечает: HTTP {health.status_code}")
            print(f"site HTTP {health.status_code}", flush=True)
        except Exception as exc:  # noqa: BLE001
            _append_md(f"Сайт недоступен: `{exc}`")
            print(f"site down: {exc}", flush=True)
            return 2

        for i, kit in enumerate(kits, start=1):
            kit_dir = session_dir / re.sub(r"[^\w\-]+", "_", kit["id"], flags=re.U)
            kit_dir.mkdir(parents=True, exist_ok=True)
            print(f"\n=== {i}/{len(kits)} {kit['id']} ({kit['profile']}) ===", flush=True)
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
                    "files": [p.name for p in list_input_files(kit["root"])[0]],
                }
                print(f"FAIL: {exc}", flush=True)
            (kit_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            block = format_result(i, len(kits), result)
            _append_md(block)
            print(block, flush=True)
            print(
                f"-> записано в {OUT_MD.name} · ok={ok} fail={fail}",
                flush=True,
            )

    summary = f"## Итог сессии `{session}`\n- ok: {ok}\n- fail/skip: {fail}\n- seed: {seed}\n"
    _append_md(summary)
    print(summary, flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
