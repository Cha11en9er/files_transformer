"""Rasterize PDF pages to temporary JPEG screenshots for a vision model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.parsing.pdf_extractor import sniff_kind

MAX_IMAGES = 20
MAX_PAGES_PER_PDF = 25
MAX_SIDE = 1600
JPEG_QUALITY = 78
RENDER_DPI = 130


@dataclass(frozen=True)
class VisionPage:
    path: Path
    source_name: str
    page: int | None


def _save_jpeg(pil_image, dest: Path) -> None:
    image = pil_image
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")
    width, height = image.size
    longest = max(width, height)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="JPEG", quality=JPEG_QUALITY, optimize=True)


def rasterize_pdf(path: Path, dest_dir: Path, *, stem: str, max_pages: int = MAX_PAGES_PER_PDF) -> list[Path]:
    pages = collect_pdf_pages(path, dest_dir, stem=stem, max_pages=max_pages)
    return [page.path for page in pages]


def collect_pdf_pages(
    path: Path,
    dest_dir: Path,
    *,
    stem: str,
    max_pages: int = MAX_PAGES_PER_PDF,
) -> list[VisionPage]:
    import pypdfium2 as pdfium

    written: list[VisionPage] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        page_count = min(len(pdf), max_pages)
        for index in range(page_count):
            page = pdf[index]
            try:
                bitmap = page.render(scale=RENDER_DPI / 72)
                pil_image = bitmap.to_pil()
                out = dest_dir / f"{stem}_p{index + 1}.jpg"
                _save_jpeg(pil_image, out)
                written.append(VisionPage(path=out, source_name=path.name, page=index + 1))
            finally:
                page.close()
    finally:
        pdf.close()
    return written


def _sheet_font(size: int):
    from PIL import ImageFont

    for candidate in (
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


_PAGE_W = 1500
_PAGE_H = 1900
_CELL_PAD = 6
_MAX_CELL_W = 420


def _cell_text(value: Any) -> str:
    text = "" if value is None else str(value).replace("\r", "")
    text = text.strip()
    if not text or text.lower() == "none":
        return ""
    return text


def _wrap(text: str, font, width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ") or [""]
        current = ""
        for word in words:
            trial = word if not current else f"{current} {word}"
            if font.getlength(trial) <= width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


def collect_workbook_pages(path: Path, dest_dir: Path, *, limit: int) -> list[VisionPage]:
    """Tile each sheet by page size so a long or wide row stays readable."""
    from PIL import Image, ImageDraw

    from app.transform.reader import read_workbook

    sheets = read_workbook(str(path))
    font = _sheet_font(18)
    line_h = 22
    pages: list[VisionPage] = []
    page_no = 0
    for sheet in sheets:
        if len(pages) >= limit:
            break
        grid = [[_cell_text(cell) for cell in row] for row in sheet.grid]
        width = max((len(row) for row in grid), default=0)
        if width == 0:
            continue
        col_w: list[int] = []
        for col in range(width):
            longest = 40
            for row in grid[:400]:
                if col < len(row) and row[col]:
                    longest = max(longest, int(font.getlength(row[col][:80])) + _CELL_PAD * 2)
            col_w.append(min(_MAX_CELL_W, longest))
        groups: list[tuple[int, int]] = []
        start = 0
        used = 0
        for col, cell_w in enumerate(col_w):
            if start < col and used + cell_w > _PAGE_W - 16:
                groups.append((start, col))
                start = col
                used = 0
            used += cell_w
        groups.append((start, width))
        row_index = 0
        while row_index < len(grid) and len(pages) < limit:
            for c0, c1 in groups:
                if len(pages) >= limit:
                    break
                slice_rows: list[list[str]] = []
                height = 36
                cursor = row_index
                while cursor < len(grid):
                    wrapped = [
                        _wrap(grid[cursor][col] if col < len(grid[cursor]) else "", font, col_w[col] - _CELL_PAD * 2)
                        for col in range(c0, c1)
                    ]
                    row_h = max((len(lines) for lines in wrapped), default=1) * line_h + _CELL_PAD
                    if slice_rows and height + row_h > _PAGE_H - 12:
                        break
                    slice_rows.append(grid[cursor])
                    height += row_h
                    cursor += 1
                if not any(any(cell for cell in row[c0:c1]) for row in slice_rows):
                    continue
                page_no += 1
                image = Image.new("RGB", (_PAGE_W, max(height, 80)), "white")
                draw = ImageDraw.Draw(image)
                title = f"{path.name} / {sheet.name}  rows {row_index + 1}-{cursor}  cols {c0 + 1}-{c1}"
                draw.text((8, 6), title, fill="black", font=font)
                y = 32
                for row in slice_rows:
                    wrapped = [
                        _wrap(row[col] if col < len(row) else "", font, col_w[col] - _CELL_PAD * 2)
                        for col in range(c0, c1)
                    ]
                    row_h = max((len(lines) for lines in wrapped), default=1) * line_h + _CELL_PAD
                    x = 8
                    for col, lines in enumerate(wrapped):
                        draw.rectangle((x, y, x + col_w[c0 + col], y + row_h), outline="#cccccc")
                        for line_i, line in enumerate(lines):
                            draw.text((x + _CELL_PAD, y + 2 + line_i * line_h), line, fill="black", font=font)
                        x += col_w[c0 + col]
                    y += row_h
                out = dest_dir / f"{path.stem[:28] or 'xls'}_p{page_no}.jpg"
                _save_jpeg(image, out)
                pages.append(VisionPage(path=out, source_name=path.name, page=page_no))
            row_index = cursor
    return pages


def collect_vision_images(paths: list[Path], dest_dir: Path) -> list[VisionPage]:
    """JPEG set for the model: PDF page photos, uploaded images, and each Excel sheet."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    collected: list[VisionPage] = []
    for path in paths:
        if len(collected) >= MAX_IMAGES:
            break
        kind = sniff_kind(str(path))
        try:
            if kind == "pdf":
                remaining = MAX_IMAGES - len(collected)
                collected.extend(
                    collect_pdf_pages(
                        path,
                        dest_dir,
                        stem=path.stem[:40] or "pdf",
                        max_pages=min(MAX_PAGES_PER_PDF, remaining),
                    )
                )
            elif kind == "excel":
                lowered = path.name.lower()
                if any(token in lowered for token in ("сводная", "справочник", "catalog", "catalogue")):
                    continue
                remaining = MAX_IMAGES - len(collected)
                collected.extend(collect_workbook_pages(path, dest_dir, limit=remaining))
            elif kind == "image":
                from PIL import Image

                out = dest_dir / f"{path.stem[:40] or 'image'}.jpg"
                with Image.open(path) as image:
                    _save_jpeg(image, out)
                collected.append(VisionPage(path=out, source_name=path.name, page=None))
        except Exception:
            continue
    return collected[:MAX_IMAGES]
