from __future__ import annotations

from datetime import datetime, timezone
import re

from sqlalchemy.orm import Session

from ..models import Collection, Document, MediaItem, Question


def build_context(db: Session, collection: Collection, config: dict) -> dict:
    """Build the data context passed to the Jinja2 template."""
    docs = (
        db.query(Document)
        .filter(Document.collection_id == collection.id)
        .order_by(Document.year.asc(), Document.id.asc())
        .all()
    )
    processed = [d for d in docs if d.status == "done"]

    questions = (
        db.query(Question)
        .join(Document, Question.document_id == Document.id)
        .filter(Document.collection_id == collection.id, Question.status.in_(["approved", "review", "pending"]))
        .all()
    )

    media_ids = []
    for q in questions:
        media_ids.extend(q.media_ids or [])
    media_map = {}
    if media_ids:
        for m in db.query(MediaItem).filter(MediaItem.id.in_(set(media_ids))).all():
            media_map[m.id] = m

    # ---- subject metadata ----
    subject_code = config.get("subject_code") or _first_nonempty(processed, "subject_code")
    subject_name = config.get("subject_name") or _first_nonempty(processed, "subject_name")
    semester = config.get("semester") or _first_nonempty(processed, "semester")
    degree = config.get("degree") or _first_nonempty(processed, "degree")
    course = config.get("course") or (degree or "B.Tech / M.Tech")

    years = sorted({d.year for d in processed if d.year})
    exam_sessions = [d.exam_session for d in processed if d.exam_session]

    # ---- grouping ----
    group_by = config.get("group_by", "part")
    groups = _group_questions(questions, group_by)

    # ---- stats / weightage (computed on deduplicated questions) ----
    part_stats = {}
    total_questions = 0
    total_marks = 0
    for group in groups:
        for q in group["questions"]:
            part = (q.get("part") or "?").upper()
            st = part_stats.setdefault(part, {"count": 0, "marks": 0})
            st["count"] += 1
            st["marks"] += q.get("marks") or 0
            total_questions += 1
            total_marks += q.get("marks") or 0

    watermark = config.get("watermark") or {}

    now = datetime.now(timezone.utc)

    context = {
        "logo_url": _file_url_or_none("assets/studique-logo.png"),
        "collection": collection,
        "config": config,
        "meta": {
            "bank_title": config.get("bank_title") or "QUESTION BANK",
            "subject_name": subject_name or "Unknown Subject",
            "subject_code": subject_code or "",
            "course": course or "",
            "semester": semester or "",
            "coverage": config.get("coverage") or "All Units",
            "years": years,
            "exam_sessions": exam_sessions,
            "notes": config.get("notes") or "",
            "compiled_on": now.strftime("%d %B %Y"),
            "source_count": len(processed),
            "instructions": _collect_instructions(processed),
            "max_marks": config.get("max_marks") or _first_nonempty(processed, "max_marks"),
            "time": config.get("time") or _first_nonempty(processed, "time_limit"),
        },
        "stats": {
            "total_questions": total_questions,
            "total_marks": total_marks,
            "part_stats": [{"part": k, "count": v["count"], "marks": v["marks"]} for k, v in sorted(part_stats.items())],
        },
        "groups": groups,
        "watermark": watermark,
        "media_map": {mid: _media_view(m) for mid, m in media_map.items()},
    }
    return context


def _first_nonempty(items: list, field: str):
    for item in items:
        v = getattr(item, field, None)
        if v:
            return v
    return None


def _collect_instructions(docs) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for d in docs:
        for ins in (d.meta.get("instructions") or []):
            key = re.sub(r"[^a-z0-9]", "", str(ins).lower())
            if key and key not in seen:
                seen.add(key)
                out.append(str(ins))
    return out


def _sort_qs(qs):
    def key(q):
        try:
            return (int(str(q.number).split(".")[0]), q.document.year or 0, int(q.document.id))
        except ValueError:
            return (10**9, q.document.year or 0, int(q.document.id))

    return sorted(qs, key=key)


def _group_questions(questions, group_by: str) -> list[dict]:
    if group_by == "year":
        buckets: dict = {}
        for q in questions:
            key = str(q.document.year or "Unknown")
            buckets.setdefault(key, []).append(q)
        return [{"title": f"Year {k}", "questions": _assign_seq_nums(v)} for k, v in sorted(buckets.items())]

    if group_by == "type":
        buckets = {}
        for q in questions:
            key = "MCQ" if q.options else "Descriptive"
            buckets.setdefault(key, []).append(q)
        return [{"title": k, "questions": _assign_seq_nums(v)} for k, v in buckets.items()]

    if group_by == "none":
        return [{"title": "Questions", "questions": _assign_seq_nums(questions)}]

    # default: by part
    buckets = {}
    for q in questions:
        key = (q.part or "?").upper()
        buckets.setdefault(key, []).append(q)
    parts = sorted(buckets.keys())
    return [{"title": f"PART - {p}", "questions": _assign_seq_nums(buckets[p])} for p in parts]


def _assign_seq_nums(qs):
    """Assign sequential numbering (1,2,3...) within a group, preserving original number + year for reference."""
    q_dicts = []
    for i, q in enumerate(_sort_qs(qs)):
        q_dict = {
            "id": q.id,
            "number": str(i + 1),
            "original_number": q.number or f"Q{i+1}",
            "numbering": str(i + 1),
            "part": q.part,
            "marks": q.marks,
            "text": q.text or "",
            "options": list(q.options or []),
            "subs": list(q.subs or []),
            "media_ids": list(q.media_ids or []),
            "confidence": dict(q.confidence or {}),
            "flags": list(q.flags or []),
            "document": q.document,
            "source_label": _source_label(q.document),
            "_sources": [],        # populated by _deduplicate_repeated
            "_repeat_count": 1,    # default; updated by _deduplicate_repeated
        }
        q_dicts.append(q_dict)
    # deduplicate repeated questions across papers (same question in multiple years)
    q_dicts = _deduplicate_repeated(q_dicts)
    # renumber after dedup
    for i, q in enumerate(q_dicts):
        q["number"] = str(i + 1)
        q["numbering"] = str(i + 1)
    return q_dicts


def _norm_for_match(text: str) -> str:
    t = text.lower()
    for cmd in [r"\left", r"\right", r"\,", r"\;", r"\!", r"\quad", r"\qquad", r"\(", r"\)", r"\[", r"\]"]:
        t = t.replace(cmd, "")
    t = re.sub(r"\s+", "", t)
    return t


def _deduplicate_repeated(q_dicts: list[dict]) -> list[dict]:
    """Merge questions that repeat across multiple papers into one entry with combined sources."""
    import difflib
    result: list[dict] = []
    sigs: list[tuple[str, int]] = []
    for q in q_dicts:
        sig = _norm_for_match(q["text"])
        if not sig:
            # empty-text questions (subs only) can't be matched reliably — keep separate
            q["_sources"] = [q["source_label"]]
            q["_repeat_count"] = 1
            result.append(q)
            continue
        best = None
        for s, idx in sigs:
            if difflib.SequenceMatcher(None, s, sig).ratio() >= 0.9:
                best = idx
                break
        if best is not None:
            result[best]["_sources"].append(q["source_label"])
            result[best]["_repeat_count"] += 1
        else:
            q["_sources"] = [q["source_label"]]
            q["_repeat_count"] = 1
            sigs.append((sig, len(result)))
            result.append(q)
    for q in result:
        if len(q["_sources"]) > 1:
            q["source_label"] = _combine_sources(q["_sources"])
    return result


def _combine_sources(sources: list[str]) -> str:
    """Combine source labels concisely, grouping sessions by year so a year is never repeated.

    e.g. (May 2023, July 2023, May 2024) -> "2023 (May, July); 2024 (May)"
    """
    degree: str | None = None
    by_year: dict[str, list[str]] = {}
    for s in sources:
        m = re.search(r"Degree Examination,?\s+([A-Z]+)\s+(\d{4})", s, re.I)
        if m:
            month, year = m.group(1).capitalize(), m.group(2)
            by_year.setdefault(year, []).append(month)
        if not degree:
            m2 = re.search(r"(.+?)\s*Degree Examination", s, re.I)
            degree = m2.group(1).strip() if m2 else None

    if not by_year:
        return "; ".join(dict.fromkeys(sources))

    parts = []
    for year in sorted(by_year):
        months = sorted(set(by_year[year]))
        parts.append(f"{year} ({', '.join(months)})" if len(months) > 1 else f"{months[0]} {year}")

    base = f"{degree} Degree Examination" if degree else "Degree Examination"
    return f"{base}, {', '.join(parts)}"


def _source_label(doc) -> str:
    """Human-readable source: university degree + exam session (e.g. 'B.Tech/M.Tech (Integrated) Degree Examination, MAY 2023')."""
    parts = []
    if doc.degree:
        parts.append(doc.degree)
    if doc.exam_session:
        session = doc.exam_session
        if "degree examination" not in session.lower():
            session = f"Degree Examination, {session}" if session else session
        parts.append(session)
    return " ".join(parts).strip() or (doc.filename or "")


def _media_view(m: MediaItem) -> dict:
    from ..storage import storage

    return {
        "id": m.id,
        "type": m.media_type,
        "thumb_url": _as_uri(storage.path(m.thumb_key or m.orig_key)),
        "orig_url": _as_uri(storage.path(m.orig_key)),
        "caption": m.caption,
    }


def _as_uri(p) -> str:
    return p.resolve().as_uri()


def _file_url_or_none(path: str):
    from ..config import BASE_DIR

    p = BASE_DIR / path
    if p.exists():
        return p.resolve().as_uri()
    return None