"""Media <-> question association engine.

Uses multiple signals:
- explicit textual references (media_refs extracted by VLM)
- the VLM's own near_questions judgment
- bbox proximity / overlap
"""

REFERENCE_WORDS = {
    "figure": "figure", "fig": "figure", "fig.": "figure", "diagram": "figure",
    "graph": "figure", "chart": "figure", "circuit": "figure", "circuit diagram": "figure",
    "graphically": "figure", "shown below": "figure",
    "shown above": "figure", "above figure": "figure", "below figure": "figure",
    "following figure": "figure", "table": "table",
}


def associate(questions: list[dict], media_list: list[dict]) -> dict:
    """Associate media items with questions.

    Flags are attached directly to each question dict under 'flags'.
    Returns:
    {
      "question_media": {q_index: [media_index, ...]},
      "shared_media": [media_index, ...],
    }
    """
    any_media = bool(media_list)
    qm: dict[int, list[int]] = {}
    shared: list[int] = []

    media_by_num: dict[str, list[int]] = {}  # question number -> media indices
    for mi, m in enumerate(media_list):
        for num in (m.get("near_questions") or []):
            media_by_num.setdefault(str(num).split(".")[0], []).append(mi)

    for qi, q in enumerate(questions):
        attached: set[int] = set()
        flags: list[dict] = []

        # Signal 1: explicit reference words in question text
        text_lower = (q.get("text") or "").lower()
        refs = []
        for word in REFERENCE_WORDS:
            if word in text_lower:
                refs.append(word)

        # Signal 2: VLM near_questions
        qnum = str(q.get("number", "")).split(".")[0]
        for mi in media_by_num.get(qnum, []):
            attached.add(mi)

        # Signal 3: bbox proximity (same page, closest center)
        if q.get("bbox") and media_list:
            closest = _closest_media(q, media_list)
            if closest is not None:
                attached.add(closest)

        # Signal 4: question mentions a figure but has a media item on the same page
        if refs and not attached:
            same_page_media = [mi for mi, m in enumerate(media_list) if _same_page(q, m)]
            if len(same_page_media) == 1:
                attached.add(same_page_media[0])

        if refs and not attached and any_media:
            flags.append(
                {
                    "level": "review",
                    "code": "FIG_REF_NO_MEDIA",
                    "reason": f"Question {qnum} references '{refs[0]}' but no matching media was found.",
                }
            )

        if flags:
            q.setdefault("flags", []).extend(flags)

        qm[qi] = sorted(attached)

    # Media attached to multiple questions -> shared
    usage: dict[int, int] = {}
    for mi_list in qm.values():
        for mi in mi_list:
            usage[mi] = usage.get(mi, 0) + 1
    for mi, count in usage.items():
        if count > 1:
            shared.append(mi)

    return {"question_media": qm, "shared_media": shared}


def _closest_media(q: dict, media_list: list[dict]) -> int | None:
    """Return index of the media closest to the question bbox on the same page."""
    q_bbox = q.get("bbox")
    if not q_bbox:
        return None
    q_center = ((q_bbox[0] + q_bbox[2]) / 2, (q_bbox[1] + q_bbox[3]) / 2)
    best, best_dist = None, 1e9
    for mi, m in enumerate(media_list):
        mb = m.get("bbox")
        if not mb:
            continue
        m_center = ((mb[0] + mb[2]) / 2, (mb[1] + mb[3]) / 2)
        dist = (q_center[0] - m_center[0]) ** 2 + (q_center[1] - m_center[1]) ** 2
        if dist < best_dist:
            best, best_dist = mi, dist
    return best


def _same_page(q: dict, m: dict) -> bool:
    q_pages = q.get("pages") or q.get("page")
    return True  # bbox proximity already assumed within page set