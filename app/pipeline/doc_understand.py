from pathlib import Path

from ..llm import llm


def understand_page(image_path: Path, page_num: int, total_pages: int, strong: bool = False) -> dict:
    """Use VLM to understand a page and return structured JSON."""
    result = llm.understand_page(image_path, page_num, total_pages, strong)
    if result is None:
        return {"page": page_num, "document_header": {}, "sections": [], "questions": [], "media": [], "flags": ["VLM unavailable"]}
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