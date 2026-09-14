import json
import re
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..llm import llm
from ..models import Document, Job, MediaItem, Page, Question, QuestionVersion
from ..storage import storage
from . import associate, doc_understand, media_extract, merge, postprocess, rasterize, validate


def process_document(db: Session, document: Document, source_path: Path) -> dict:
    """Run the full extraction pipeline for a document."""
    try:
        pages = rasterize.ingest_pdf(source_path, document.id)
        db.query(Page).filter(Page.document_id == document.id).delete()

        page_rows = []
        for p in pages:
            row = Page(
                document_id=document.id,
                page_number=p["page_number"],
                width=p["width"],
                height=p["height"],
                render_key=p["render_key"],
                has_text_layer=p["has_text_layer"],
                text_layer=p["text_layer"],
            )
            db.add(row)
            page_rows.append(row)
        document.page_count = len(pages)
        db.flush()

        # ---- OCR scanned pages (no text layer) so part headers are always readable ----
        for row, p in zip(page_rows, pages):
            if not row.text_layer:
                from .ocr import ocr_page

                ocr_text = ocr_page(storage.path(p["render_key"]))
                row.text_layer = ocr_text or ""
                row.has_text_layer = False
        db.flush()

        # ---- VLM page understanding ----
        render_paths = [storage.path(p["render_key"]) for p in pages]
        page_results = []
        for i, rp in enumerate(render_paths):
            raw = doc_understand.understand_page(rp, i, len(render_paths))
            normalized = doc_understand.normalize_page_result(raw)
            if _has_empty_questions(normalized):
                raw2 = doc_understand.understand_page(rp, i, len(render_paths), strong=True)
                normalized2 = doc_understand.normalize_page_result(raw2)
                if _empty_count(normalized2) < _empty_count(normalized):
                    normalized = normalized2
            page_results.append(normalized)

        # First page header -> document metadata
        header = page_results[0].get("document_header") or {}
        _apply_header(db, document, header)
        db.flush()

        # ---- Merge across pages ----
        questions = merge.merge_pages(page_results)

        # ---- Post-processing: fix parts, marks, coalesce same-numbered subs ----
        texts = [row.text_layer or "" for row in page_rows]
        num_map, part_defaults = postprocess.compute_part_map_from_text(texts, questions)
        if not num_map:
            sections = []
            for pr in page_results:
                sections.extend(pr.get("sections") or [])
            num_map, part_defaults = postprocess.compute_part_map(sections, questions)
        postprocess.fix_parts(questions, num_map)
        postprocess.apply_marks_defaults(questions, part_defaults)
        questions = postprocess.normalize_leading_subs(questions)
        questions = postprocess.coalesce_subs(questions)
        questions = postprocess.clean_options(questions)
        questions = postprocess.strip_inline_option_run(questions)
        questions = postprocess.repair_math_delimiters(questions)

        # ---- Media extraction (crops at high res) ----
        all_media = []
        media_row_by_key = {}
        for pr in page_results:
            pn = pr.get("page", 0)
            if not pr.get("media"):
                continue
            hi_path = rasterize.render_page_hi(str(source_path), document.id, pn)
            page_row = page_rows[pn] if pn < len(page_rows) else page_rows[-1]
            for m in pr["media"]:
                media_id = f"m_{document.id}_{uuid.uuid4().hex[:8]}"
                try:
                    orig_key, thumb_key = media_extract.crop_media(hi_path, m["bbox"], media_id, m.get("type", "figure"))
                except Exception:
                    continue
                row = MediaItem(
                    document_id=document.id,
                    page_id=page_row.id,
                    media_type=m.get("type", "figure"),
                    bbox=m.get("bbox", [0, 0, 1, 1]),
                    orig_key=orig_key,
                    thumb_key=thumb_key,
                    caption=m.get("caption"),
                    confidence=float(m.get("conf", 0.9)),
                )
                db.add(row)
                db.flush()
                all_media.append({"row": row, "near": m.get("near_questions") or [], "type": m.get("type", "figure")})
                media_row_by_key[row.id] = row

        # ---- Association ----
        assoc = associate.associate(questions, all_media)

        # ---- Validation ----
        validate.validate_questions(questions)

        # ---- Persist questions ----
        db.query(Question).filter(Question.document_id == document.id).delete()
        for qi, q in enumerate(questions):
            media_ids = []
            for mi in assoc["question_media"].get(qi, []):
                row = all_media[mi]["row"]
                media_ids.append(row.id)
            if mi_assoc := _find_media_by_near(all_media, q, assoc):
                for r in mi_assoc:
                    if r.id not in media_ids:
                        media_ids.append(r.id)

            for mi in assoc["shared_media"]:
                if mi < len(all_media):
                    all_media[mi]["row"].shared = True

            conf = validate.compute_confidence(q)
            flags = q.get("flags") or []

            q_row = Question(
                document_id=document.id,
                page_ids=q.get("pages", []),
                part=q.get("part", ""),
                number=q.get("number", ""),
                numbering=str(q.get("number", "")),
                title="",
                marks=q.get("marks"),
                text=postprocess.wrap_bare_math(q.get("text", "")),
                options=[postprocess.wrap_bare_math(o) for o in q.get("options", [])],
                subs=[
                    {**s, "text": postprocess.wrap_bare_math(s.get("text", ""))}
                    for s in q.get("subs", [])
                ],
                media_ids=media_ids,
                media_refs=q.get("media_refs", []),
                meta={"bbox": q.get("bbox", [0, 0, 1, 1]), "incomplete": q.get("incomplete", False), "continue_next": q.get("continue_next", False)},
                confidence=conf,
                flags=flags,
                status="approved" if conf["overall"] >= 0.8 and not flags else "review",
                version=1,
            )
            db.add(q_row)

        # Deduplicate shared marking
        for mrow in all_media:
            pass

        db.flush()
        db.commit()
        document.status = "done"
        meta = dict(document.meta)
        meta["warnings"] = validate.check_document_part_counts(questions, postprocess.parse_part_structure(texts))
        document.meta = meta
        db.commit()
        return {"status": "done", "questions": len(questions), "media": len(all_media), "pages": len(pages)}
    except Exception as exc:
        db.rollback()
        document.status = "error"
        document.error = str(exc)
        db.commit()
        return {"status": "error", "error": str(exc)}


def _find_media_by_near(all_media, q, assoc):
    qnum = str(q.get("number", "")).split(".")[0]
    return [m["row"] for m in all_media if str(qnum) in [str(n) for n in m["near"]]]


def _has_empty_questions(page_result: dict) -> bool:
    return any(not (q.get("text") or "").strip() for q in page_result.get("questions", []))


def _empty_count(page_result: dict) -> int:
    return sum(1 for q in page_result.get("questions", []) if not (q.get("text") or "").strip())


def _apply_header(db: Session, document: Document, header: dict) -> None:
    document.subject_code = header.get("subject_code") or document.subject_code
    document.subject_name = header.get("subject_name") or document.subject_name
    document.exam_session = header.get("exam_session") or document.exam_session
    document.semester = header.get("semester") or document.semester
    document.time_limit = header.get("time") or document.time_limit
    document.max_marks = header.get("max_marks") or document.max_marks
    document.degree = header.get("degree") or document.degree
    if document.exam_session:
        m = re.search(r"(19|20)\d{2}", document.exam_session)
        if m:
            document.year = int(m.group(0))
    document.meta = {**document.meta, "instructions": header.get("instructions", [])}
