from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from app.parsing.table_rows import (
    lines_from_matrix,
    lines_from_plaintext,
    lines_from_qty_price_text,
    merge_line_groups,
)

_EASYOCR_READER = None


def _reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        import easyocr

        try:
            _EASYOCR_READER = easyocr.Reader(["en", "ru", "tr"], gpu=False, verbose=False)
        except Exception:
            _EASYOCR_READER = easyocr.Reader(["en", "ru"], gpu=False, verbose=False)
    return _EASYOCR_READER


def _preprocess(path: str):
    from PIL import Image, ImageOps

    image = Image.open(path)
    if image.mode not in {"L", "RGB"}:
        image = image.convert("RGB")
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    if min(gray.size) < 900:
        scale = 900 / max(min(gray.size), 1)
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)))
    return gray.convert("RGB")


def _box_xy(box: Any) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return min(xs), min(ys), max(xs), max(ys)


def rows_from_ocr_blocks(blocks: list[dict[str, Any]]) -> list[list[str]]:
    """Group EasyOCR boxes into table-like rows (top-to-bottom, left-to-right)."""
    if not blocks:
        return []
    items: list[tuple[float, float, float, str]] = []
    heights: list[float] = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        x0, y0, _x1, y1 = _box_xy(block["box"])
        height = max(y1 - y0, 1.0)
        heights.append(height)
        items.append((y0, x0, height, text))
    if not items:
        return []
    heights.sort()
    median_h = heights[len(heights) // 2]
    thresh = max(median_h * 0.65, 10.0)
    items.sort(key=lambda item: (item[0], item[1]))
    rows: list[list[tuple[float, str]]] = []
    current: list[tuple[float, str]] = []
    current_y: float | None = None
    for y0, x0, _height, text in items:
        if current_y is None or abs(y0 - current_y) <= thresh:
            current.append((x0, text))
            current_y = y0 if current_y is None else (current_y * 0.6 + y0 * 0.4)
        else:
            rows.append(current)
            current = [(x0, text)]
            current_y = y0
    if current:
        rows.append(current)
    return [[text for _x, text in sorted(row)] for row in rows]


def lines_from_ocr_blocks(blocks: list[dict[str, Any]], *, sheet_name: str = "ocr") -> list[dict[str, Any]]:
    matrix = rows_from_ocr_blocks(blocks)
    text = "\n".join(" ".join(row) for row in matrix)
    return merge_line_groups(
        lines_from_matrix(matrix, sheet_name=f"{sheet_name}_table"),
        lines_from_qty_price_text(text, sheet_name=f"{sheet_name}_qty"),
        lines_from_plaintext(text, sheet_name=f"{sheet_name}_text"),
    )


def ocr_image(path: str) -> tuple[str, float | None, list[dict[str, Any]]]:
    """Run EasyOCR on a raster file. Raises ImportError if EasyOCR is not installed."""
    import numpy as np

    reader = _reader()
    prepared = _preprocess(path)
    results = reader.readtext(np.array(prepared))
    texts: list[str] = []
    confidences: list[float] = []
    blocks: list[dict[str, Any]] = []
    for box, text, conf in results:
        texts.append(text)
        confidences.append(float(conf))
        blocks.append({"text": text, "confidence": float(conf), "box": box})
    mean_conf = sum(confidences) / len(confidences) if confidences else None
    return "\n".join(texts), mean_conf, blocks


# Safety caps: full-page easyocr/torch on many high-dpi pages inside the web
# process was the cause of the "network error" (OOM/segfault dropped the stream).
# Cap the resolution and the number of pages we ever rasterize+OCR in one call.
_OCR_MAX_DPI = 200
_OCR_MAX_PAGES = 6


def ocr_pdf_pages(path: str, *, dpi: int = 280, pages: list[int] | None = None) -> tuple[str, float | None, list[dict[str, Any]]]:
    """Rasterize PDF pages with pypdfium2 and OCR them. `pages` is 1-based."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError(
            "Searchable PDF text was empty and PDF rasterization is unavailable "
            "(install pypdfium2 + easyocr for scan OCR)."
        ) from exc

    dpi = min(int(dpi), _OCR_MAX_DPI)
    pdf = pdfium.PdfDocument(path)
    page_texts: list[str] = []
    confidences: list[float] = []
    all_lines: list[dict[str, Any]] = []
    wanted = {int(p) for p in pages} if pages else None
    processed = 0
    with tempfile.TemporaryDirectory(prefix="ocr_pdf_") as tmp:
        tmp_dir = Path(tmp)
        for index, page in enumerate(pdf):
            page_no = index + 1
            if wanted is not None and page_no not in wanted:
                page.close()
                continue
            if processed >= _OCR_MAX_PAGES:
                page.close()
                break
            processed += 1
            bitmap = page.render(scale=dpi / 72)
            pil_image = bitmap.to_pil()
            image_path = tmp_dir / f"page_{index}.png"
            pil_image.save(image_path)
            try:
                text, conf, blocks = ocr_image(str(image_path))
            finally:
                page.close()
            page_texts.append(text)
            if conf is not None:
                confidences.append(conf)
            all_lines.extend(lines_from_ocr_blocks(blocks, sheet_name=f"ocr_p{page_no}"))
    pdf.close()
    mean_conf = sum(confidences) / len(confidences) if confidences else None
    return "\n".join(page_texts), mean_conf, all_lines
