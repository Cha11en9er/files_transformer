"""Rasterize PDF pages to temporary JPEG screenshots for a vision model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def collect_vision_images(paths: list[Path], dest_dir: Path) -> list[VisionPage]:
    """Build a short-lived JPEG set: PDF pages plus already uploaded images."""
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
            elif kind == "image":
                from PIL import Image

                out = dest_dir / f"{path.stem[:40] or 'image'}.jpg"
                with Image.open(path) as image:
                    _save_jpeg(image, out)
                collected.append(VisionPage(path=out, source_name=path.name, page=None))
        except Exception:
            continue
    return collected[:MAX_IMAGES]
