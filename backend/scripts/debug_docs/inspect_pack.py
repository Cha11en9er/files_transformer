"""Dump Excel / PDF / DOCX from a documents pack into UTF-8 reports.

Used to compare customer annotations, source files and etalon outputs
without relying on the console codepage.

Run from backend/:
    python scripts/debug_docs/inspect_pack.py
    python scripts/debug_docs/inspect_pack.py --root "../../documents/4_pravka"
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PACK = ROOT / "documents" / "4_pravka"
OUT_DIR = Path(__file__).resolve().parent / "out"

NS_W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}
PDF_EXT = {".pdf"}
DOCX_EXT = {".docx"}
MAX_ROWS = 40
MAX_COLS = 18
TAIL_ROWS = 8


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def dump_docx(path: Path) -> str:
    lines: list[str] = [f"FILE: {path}", f"SIZE: {path.stat().st_size}", ""]
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except Exception as exc:
        return "\n".join(lines + [f"[failed to open docx: {exc}]"])
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for para in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        texts = [
            node.text or ""
            for node in para.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        ]
        blob = "".join(texts).strip()
        if blob:
            paragraphs.append(blob)
    if not paragraphs:
        lines.append("[empty document body]")
    else:
        lines.extend(paragraphs)
    return "\n".join(lines)


def _cell(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text[:60]


def dump_xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    lines: list[str] = [f"FILE: {path.name}", f"SIZE: {path.stat().st_size}"]
    try:
        wb = load_workbook(path, data_only=True, read_only=False)
    except Exception as exc:
        return "\n".join(lines + [f"[openpyxl failed: {exc}]"])
    lines.append(f"SHEETS: {wb.sheetnames}")
    for ws in wb.worksheets:
        merges = list(ws.merged_cells.ranges)
        lines.append("")
        lines.append(f"-- sheet '{ws.title}'  dims={ws.max_row}x{ws.max_column}  merges={len(merges)}")
        if merges:
            shown = ", ".join(str(m) for m in merges[:40])
            extra = f" (+{len(merges) - 40} more)" if len(merges) > 40 else ""
            lines.append(f"   merged: {shown}{extra}")
        max_row = ws.max_row or 0
        max_col = min(ws.max_column or 0, MAX_COLS)
        head_end = min(max_row, MAX_ROWS)
        for r in range(1, head_end + 1):
            cells = [_cell(ws.cell(r, c).value) for c in range(1, max_col + 1)]
            if any(cells):
                lines.append(f"   r{r:>4}| " + " | ".join(cells))
        if max_row > MAX_ROWS + TAIL_ROWS:
            lines.append("   ...")
            for r in range(max_row - TAIL_ROWS + 1, max_row + 1):
                cells = [_cell(ws.cell(r, c).value) for c in range(1, max_col + 1)]
                if any(cells):
                    lines.append(f"   r{r:>4}| " + " | ".join(cells))
        elif max_row > MAX_ROWS:
            for r in range(MAX_ROWS + 1, max_row + 1):
                cells = [_cell(ws.cell(r, c).value) for c in range(1, max_col + 1)]
                if any(cells):
                    lines.append(f"   r{r:>4}| " + " | ".join(cells))
    wb.close()
    return "\n".join(lines)


def dump_xls(path: Path) -> str:
    import pandas as pd

    lines: list[str] = [f"FILE: {path.name}", f"SIZE: {path.stat().st_size}"]
    try:
        book = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine="xlrd")
    except Exception as exc:
        try:
            book = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
        except Exception as exc2:
            return "\n".join(lines + [f"[xls failed: {exc} / {exc2}]"])
    lines.append(f"SHEETS: {list(book)}")
    for name, frame in book.items():
        lines.append("")
        lines.append(f"-- sheet '{name}'  dims={frame.shape}")
        rows = min(len(frame), MAX_ROWS)
        cols = min(frame.shape[1], MAX_COLS)
        for r in range(rows):
            cells = []
            for c in range(cols):
                v = frame.iat[r, c]
                if v is None or (isinstance(v, float) and v != v):
                    cells.append("")
                else:
                    cells.append(_cell(v))
            if any(cells):
                lines.append(f"   r{r:>4}| " + " | ".join(cells))
        if len(frame) > MAX_ROWS:
            lines.append("   ...")
            start = max(MAX_ROWS, len(frame) - TAIL_ROWS)
            for r in range(start, len(frame)):
                cells = []
                for c in range(cols):
                    v = frame.iat[r, c]
                    if v is None or (isinstance(v, float) and v != v):
                        cells.append("")
                    else:
                        cells.append(_cell(v))
                if any(cells):
                    lines.append(f"   r{r:>4}| " + " | ".join(cells))
    return "\n".join(lines)


def dump_pdf(path: Path) -> str:
    lines: list[str] = [f"FILE: {path.name}", f"SIZE: {path.stat().st_size}"]
    try:
        import pdfplumber
    except Exception:
        pdfplumber = None
    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as pdf:
                lines.append(f"ENGINE: pdfplumber  PAGES: {len(pdf.pages)}")
                for i, page in enumerate(pdf.pages[:2]):
                    text = (page.extract_text() or "").strip()
                    lines.append(f"-- page {i + 1} text ({len(text)} chars)")
                    for line in text.splitlines()[:80]:
                        lines.append("   | " + line[:140])
                    tables = page.extract_tables() or []
                    lines.append(f"-- page {i + 1} tables={len(tables)}")
                    for ti, table in enumerate(tables[:2]):
                        lines.append(f"   table {ti + 1} ({len(table)} rows)")
                        for row in table[:15]:
                            cells = ["" if x is None else str(x).replace("\n", " ")[:22] for x in row[:MAX_COLS]]
                            lines.append("     " + " | ".join(cells))
            return "\n".join(lines)
        except Exception as exc:
            lines.append(f"[pdfplumber failed: {exc}]")
    try:
        from pypdf import PdfReader
    except Exception as exc:
        return "\n".join(lines + [f"[pdfplumber missing; pypdf missing: {exc}]"])
    try:
        reader = PdfReader(path)
        lines.append(f"ENGINE: pypdf  PAGES: {len(reader.pages)}")
        for i, page in enumerate(reader.pages[:2]):
            text = (page.extract_text() or "").strip()
            lines.append(f"-- page {i + 1} text ({len(text)} chars)")
            for line in text.splitlines()[:80]:
                lines.append("   | " + line[:140])
    except Exception as exc:
        lines.append(f"[pypdf failed: {exc}]")
    return "\n".join(lines)


def slug(rel: Path) -> str:
    text = str(rel).replace("\\", "__").replace("/", "__")
    text = re.sub(r"[^\w.\-]+", "_", text, flags=re.UNICODE)
    return text[:180]


def inspect_tree(root: Path) -> str:
    lines = [f"ROOT: {root}", ""]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        lines.append(f"{rel}  ({path.stat().st_size} bytes)  {path.suffix.lower()}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_PACK))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.exists():
        print(f"missing {root}", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write(OUT_DIR / "00_tree.txt", inspect_tree(root))
    files = [p for p in sorted(root.rglob("*")) if p.is_file()]
    index: list[str] = []
    for path in files:
        rel = path.relative_to(root)
        suffix = path.suffix.lower()
        name = slug(rel) + ".txt"
        target = OUT_DIR / name
        if suffix in DOCX_EXT:
            text = dump_docx(path)
        elif suffix in {".xlsx", ".xlsm"}:
            text = dump_xlsx(path)
        elif suffix == ".xls":
            text = dump_xls(path)
        elif suffix in PDF_EXT:
            text = dump_pdf(path)
        else:
            text = f"FILE: {path.name}\nSIZE: {path.stat().st_size}\n[skipped {suffix}]"
        _write(target, text)
        index.append(f"{rel} -> {name}")
        print(f"dumped {name}")
    _write(OUT_DIR / "00_index.txt", "\n".join(index))
    print(f"wrote {len(files)} dumps to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
