def merge_pages(page_results: list[dict]) -> list[dict]:
    """Merge per-page question lists into a single ordered list of questions,
    joining continuations that span page boundaries.

    Each input page result: {page, questions: [...], ...}
    Returns list of question dicts with a 'pages' list.
    """
    merged: list[dict] = []

    for page_result in page_results:
        pn = page_result.get("page", 0)
        for q in page_result.get("questions", []):
            q = dict(q)
            q["pages"] = [pn]

            # Continuation of the previous question (no number or flagged continuation)
            is_cont = q.get("is_continuation", False)
            has_number = bool(str(q.get("number", "")).strip())

            if merged and (
                is_cont
                or (not has_number and merged[-1].get("continue_next", False))
                or (
                    merged[-1].get("continue_next", False)
                    and has_number
                    and str(q.get("number")).split(".")[0] == str(merged[-1].get("number")).split(".")[0]
                )
            ):
                prev = merged[-1]
                _merge_into(prev, q)
            else:
                merged.append(q)

    return merged


def _merge_into(prev: dict, q: dict) -> None:
    """Merge question q into prev, preserving reading order of content."""
    if q.get("pages"):
        prev["pages"] = sorted(set(prev.get("pages", []) + q["pages"]))

    prev["continue_next"] = bool(q.get("continue_next")) or bool(prev.get("continue_next"))
    prev["incomplete"] = bool(q.get("incomplete")) or bool(prev.get("incomplete"))

    if q.get("text"):
        prev["text"] = (prev.get("text") or "") + "\n" + q["text"]

    if q.get("options") and not prev.get("options"):
        prev["options"] = q["options"]

    if q.get("subs"):
        prev["subs"] = (prev.get("subs") or []) + q["subs"]

    # Extend bbox to cover both pages (approx: keep the first page bbox)
    prev["conf"] = min(prev.get("conf", 1.0), q.get("conf", 1.0))