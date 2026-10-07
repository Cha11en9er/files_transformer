# -*- coding: utf-8 -*-
"""Снимок поставки для разбора. Путь меняется аргументом, новый файл не создавать.

python study_tools/dump_shipment.py "documents/7_pravka/Дегон"
"""
import argparse
import zipfile
from pathlib import Path

import openpyxl
import xlrd
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "out" / "dump.txt"


def cell_s(value, limit):
    if value is None:
        return ""
    text = " ".join(str(value).replace("\n", " | ").replace("\r", "").split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def write_grid(fh, rows, cols, getter, limit, row_offset=0):
    for row in range(rows):
        parts = []
        for col in range(cols):
            text = cell_s(getter(row, col), limit)
            if text:
                parts.append(f"C{col + 1}:{text}")
        if parts:
            fh.write(f"R{row_offset + row + 1}: " + " || ".join(parts) + "\n")


def dump_xlsx(path, fh, max_rows, max_cols, limit):
    fh.write(f"\n######## XLSX {path}\n")
    try:
        book = openpyxl.load_workbook(path, data_only=True)
    except Exception as error:
        fh.write(f"OPEN FAIL {error}\n")
        return
    for name in book.sheetnames:
        sheet = book[name]
        fh.write(
            f"\n--- sheet {name!r} dims={sheet.dimensions} "
            f"max_r={sheet.max_row} max_c={sheet.max_column}\n"
        )
        merges = list(sheet.merged_cells.ranges)
        fh.write(f"merges {len(merges)}\n")
        for merge in merges[:60]:
            fh.write(f"  merge {merge}\n")
        if len(merges) > 60:
            fh.write(f"  ... +{len(merges) - 60} merges\n")
        last = sheet.max_row or 0
        width = min(sheet.max_column or 0, max_cols)

        def getter(row, col, sheet=sheet):
            return sheet.cell(row + 1, col + 1).value

        write_grid(fh, min(last, max_rows), width, getter, limit)
        if last > max_rows:
            fh.write(f"... truncated, total rows {last}\n")
            start = max(max_rows, last - 8)
            fh.write(f"-- tail from R{start + 1}\n")
            write_grid(
                fh,
                last - start,
                width,
                lambda row, col, start=start: getter(start + row, col),
                limit,
                row_offset=start,
            )
    book.close()


def dump_xls(path, fh, max_rows, max_cols, limit):
    fh.write(f"\n######## XLS {path}\n")
    try:
        book = xlrd.open_workbook(str(path), formatting_info=False)
    except Exception as error:
        fh.write(f"OPEN FAIL {error}\n")
        return
    for name in book.sheet_names():
        sheet = book.sheet_by_name(name)
        fh.write(f"\n--- sheet {name!r} rows={sheet.nrows} cols={sheet.ncols}\n")
        merges = getattr(sheet, "merged_cells", []) or []
        fh.write(f"merges {len(merges)}\n")
        for top, bottom, left, right in list(merges)[:60]:
            fh.write(f"  merge r{top + 1}-{bottom} c{left + 1}-{right}\n")
        width = min(sheet.ncols, max_cols)

        def getter(row, col, sheet=sheet):
            return sheet.cell_value(row, col)

        write_grid(fh, min(sheet.nrows, max_rows), width, getter, limit)
        if sheet.nrows > max_rows:
            fh.write(f"... truncated, total rows {sheet.nrows}\n")
            start = max(max_rows, sheet.nrows - 8)
            fh.write(f"-- tail from R{start + 1}\n")
            write_grid(
                fh,
                sheet.nrows - start,
                width,
                lambda row, col, start=start: getter(start + row, col),
                limit,
                row_offset=start,
            )


def read_note(path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def dump_pdf(path, fh, pages):
    fh.write(f"\n#### PDF {path}\n")
    try:
        reader = PdfReader(str(path))
    except Exception as error:
        fh.write(f"OPEN FAIL {error}\n")
        return
    total = len(reader.pages)
    fh.write(f"pages {total}\n")
    chosen = list(range(min(pages, total)))
    if total > pages:
        chosen.append(total - 1)
    seen = set()
    for index in chosen:
        if index in seen:
            continue
        seen.add(index)
        text = reader.pages[index].extract_text() or ""
        fh.write(f"\n--- page {index + 1}/{total} ---\n")
        if len(text) > 4000:
            text = text[:4000] + "\n...[cut]\n"
        fh.write(text + "\n")


def main():
    parser = argparse.ArgumentParser(description="Снимок Excel и PDF одной папки поставки")
    parser.add_argument("path", help="Папка поставки или файл")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Куда писать снимок, файл перезаписывается")
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--cols", type=int, default=30)
    parser.add_argument("--cell", type=int, default=80, help="Сколько знаков клетки оставить")
    parser.add_argument("--pdf-pages", type=int, default=2)
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    out = Path(args.out).resolve()
    unzip_dir = out.parent / "unzip"
    out.parent.mkdir(parents=True, exist_ok=True)
    if unzip_dir.exists():
        for old in unzip_dir.rglob("*"):
            if old.is_file():
                old.unlink()

    sources = [root] if root.is_file() else list(root.rglob("*"))
    for path in sources:
        if path.suffix.lower() != ".zip":
            continue
        dest = unzip_dir / path.parent.name
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(dest)

    excel = []
    pdfs = []
    notes = []
    pool = sources + (list(unzip_dir.rglob("*")) if unzip_dir.exists() else [])
    for path in pool:
        if not path.is_file() or path.name.startswith("~$"):
            continue
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xlsm", ".xls"}:
            excel.append(path)
        elif suffix == ".pdf" and not args.no_pdf:
            pdfs.append(path)
        elif suffix == ".txt":
            notes.append(path)
    excel.sort(key=lambda item: str(item).lower())
    pdfs.sort(key=lambda item: str(item).lower())
    notes.sort(key=lambda item: str(item).lower())

    with out.open("w", encoding="utf-8") as handle:
        handle.write(f"ROOT {root}\n")
        for path in notes:
            handle.write(f"\n===== NOTE {path} =====\n")
            handle.write(read_note(path) + "\n")
        for path in excel:
            print("dump", path.name)
            if path.suffix.lower() == ".xls":
                dump_xls(path, handle, args.rows, args.cols, args.cell)
            else:
                dump_xlsx(path, handle, args.rows, args.cols, args.cell)
        for path in pdfs:
            print("pdf", path.name)
            dump_pdf(path, handle, args.pdf_pages)
    print("wrote", out, "bytes", out.stat().st_size)


if __name__ == "__main__":
    main()
