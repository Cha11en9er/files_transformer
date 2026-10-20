"""Один прогон Weavers (5_pravka) на :8200. Вход: INV+PL PDF + SPEC xlsx."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "documents" / "5_pravka" / "!Weavers"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://87.251.86.53:8200"
OUT = ROOT / "roadmap" / "weavers_once.md"

FILES = [
    KIT / "WEAVERS INVOICE.pdf",
    KIT / "WEAVERS PLIST.pdf",
    KIT / "Specification для 17768 WEAVERS.xlsx",
]


def main() -> int:
    missing = [p for p in FILES if not p.is_file()]
    if missing:
        print("нет файлов:", missing)
        return 2

    print("base", BASE)
    print("files", [p.name for p in FILES])
    try:
        health = httpx.get(f"{BASE.rstrip('/')}/", timeout=20.0)
        print("health", health.status_code)
    except Exception as exc:
        print("сайт недоступен:", exc)
        OUT.write_text(
            f"# Weavers once\n\nСайт `{BASE}` недоступен: `{exc}`\n\n"
            "На сервере: `sudo systemctl restart excel-transformer-update` "
            "(и при необходимости `sudo systemctl restart opencode`).\n",
            encoding="utf-8",
        )
        return 2

    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in FILES]
    t0 = time.time()
    chunks: list[str] = []
    with httpx.Client(timeout=None) as client:
        with client.stream(
            "POST",
            f"{BASE.rstrip('/')}/api/v1/shipments/",
            data={"title": "weavers-17768", "profile_type": "18233", "stream": "1"},
            files=files,
        ) as resp:
            print("post", resp.status_code)
            for chunk in resp.iter_text():
                chunks.append(chunk)
                if time.time() - t0 > 30 and len(chunks) % 20 == 0:
                    print(f"  … {int(time.time()-t0)}s still streaming", flush=True)
            body = "".join(chunks)
            resp.raise_for_status()

    elapsed = time.time() - t0
    done = None
    stages = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "progress":
            stages.append(ev.get("message"))
            print("progress:", ev.get("stage"), ev.get("message"), flush=True)
        elif ev.get("event") == "done":
            done = {k: v for k, v in ev.items() if k != "event"}
        elif ev.get("event") == "error":
            raise RuntimeError(ev.get("detail"))

    if done is None and body.strip().startswith("{"):
        done = json.loads(body)

    items = (done or {}).get("items") or []
    header = (done or {}).get("header_fields") or {}
    review = (done or {}).get("model_review") or {}
    lines = [
        "# Weavers once",
        "",
        f"- base: `{BASE}`",
        f"- files: {[p.name for p in FILES]}",
        f"- elapsed: **{elapsed:.1f}s**",
        f"- items: {len(items)}",
        f"- invoice: `{header.get('invoice_no')}`",
        f"- seller: {(header.get('seller') or '')[:120]}",
        f"- buyer: {(header.get('buyer') or '')[:120]}",
        f"- model: {review.get('status')} / {review.get('model_label')}",
        f"- meaning: {(review.get('meaning') or '')[:400]}",
        f"- error: {review.get('error')}",
        "",
        "## sample",
        "```json",
        json.dumps(
            [
                {
                    "article": (it.get("commercial_data") or {}).get("article"),
                    "qty": (it.get("commercial_data") or {}).get("qty"),
                    "amount": (it.get("commercial_data") or {}).get("amount"),
                    "packages": (it.get("packing_data") or {}).get("rolls")
                    or (it.get("packing_data") or {}).get("boxes"),
                }
                for it in items[:10]
            ],
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## last stages",
        *[f"- {s}" for s in stages[-12:]],
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT)
    print("items", len(items), "elapsed", round(elapsed, 1))
    return 0 if items else 1


if __name__ == "__main__":
    raise SystemExit(main())
