from __future__ import annotations

import re
from pathlib import Path

from ..llm import llm

# Heuristic: if a page's OCR/text layer contains these patterns it is math-heavy
# and should use the strong model for better LaTeX extraction.
_MATH_HEAVY_RE = re.compile(
    r"[∫∑∏√∞∂∇αβγδεζηθλμνξπρστφψω]"  # Unicode math chars
    r"|d/dx|dy/dx|d²|d\^2"             # derivative notation
    r"|\\frac|\\int|\\sum|\\lim"        # already-latex fragments
    r"|\blaplace\b|\bfourier\b|\bmatrix\b|\bdeterminant\b"  # keywords
    r"|\beigenvalue\b|\beigenvector\b"
    r"|[A-Za-z]\^[0-9{]|\([0-9]+\s*/\s*[0-9]+\)",  # exponent or fraction-like
    re.IGNORECASE,
)


def _is_math_heavy(text_layer: str | None) -> bool:
    """Return True if the page's text layer suggests heavy math content."""
    if not text_layer:
        return False
    return bool(_MATH_HEAVY_RE.search(text_layer))


def understand_page(
    image_path: Path,
    page_num: int,
    total_pages: int,
    strong: bool = False,
    text_hint: str | None = None,
) -> dict:
    """Use VLM to understand a page and return structured JSON.

    Args:
        image_path: Path to the rendered page image.
        page_num: 0-based page index.
        total_pages: Total pages in the document.
        strong: Force the strong (more capable) model.
        text_hint: Optional OCR/text-layer content used to detect math-heavy pages.
    """
    # Auto-upgrade to strong model for pages with heavy math notation
    if not strong and _is_math_heavy(text_hint):
        strong = True

    result = llm.understand_page(image_path, page_num, total_pages, strong)
    if result is None:
        return {
            "page": page_num,
            "document_header": {},
            "sections": [],
            "questions": [],
            "media": [],
            "flags": ["VLM unavailable"],
        }
    return result


def normalize_page_result(page_result: dict) -> dict:
    """Normalize the VLM page result into a consistent shape, filling defaults."""
    return {
        "page": page_result.get("page", 0),
        "document_header": page_result.get("document_header") or {},
        "sections": page_result.get("sections") or [],
        "questions": _normalize_questions(page_result.get("questions") or []),
        "media": page_result.get("media") or [],
        "flags": page_result.get("flags") or [],
    }


def _normalize_questions(questions: list) -> list:
    out = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        out.append(
            {
                "number": q.get("number", ""),
                "part": q.get("part", ""),
                "marks": q.get("marks"),
                "text": q.get("text", ""),
                "options": q.get("options") or [],
                "subs": q.get("subs") or [],
                "is_continuation": bool(q.get("is_continuation")),
                "continue_next": bool(q.get("continue_next")),
                "incomplete": bool(q.get("incomplete")),
                "media_refs": q.get("media_refs") or [],
                "bbox": q.get("bbox") or [0, 0, 1, 1],
                "conf": q.get("conf", 0.0),
            }
        )
    return out
