import pymupdf
from pathlib import Path

from ..config import settings
from ..storage import storage


def ingest_pdf(src_path: Path, document_id: int) -> list[dict]:
    """Open a PDF, render every page at 200dpi to storage, and extract text layers.

    Returns a list of page dicts:
    {page_number, width, height, render_key, has_text_layer, text_layer}
    """
    pages = []
    doc = pymupdf.open(str(src_path))
    try:
        for i in range(doc.page_count):
            page = doc[i]
            rect = page.rect
            pix = page.get_pixmap(dpi=250)
            key = f"docs/{document_id}/render_p{i}.png"
            data = pix.tobytes("png")
            storage.put_bytes(key, data)

            text = page.get_text()
            has_text = bool(text and text.strip())

            pages.append(
                {
                    "page_number": i,
                    "width": int(rect.width),
                    "height": int(rect.height),
                    "render_key": key,
                    "has_text_layer": has_text,
                    "text_layer": text if has_text else None,
                }
            )
    finally:
        doc.close()
    return pages


def render_page_hi(page_path: str, document_id: int, page_number: int) -> Path:
    """Render one page at 300 dpi for high-quality media crops."""
    doc = pymupdf.open(page_path)
    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=300)
        key = f"docs/{document_id}/hi_p{page_number}.png"
        storage.put_bytes(key, pix.tobytes("png"))
        return storage.path(key)
    finally:
        doc.close()
