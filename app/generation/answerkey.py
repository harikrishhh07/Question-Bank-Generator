from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Answer, Collection, Document, QbGeneration, Question
from ..storage import storage
from . import document
from .context import _file_url_or_none, _group_questions
from .document import _resolve_watermark

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def generate_answerkey(db: Session, collection_id: int, config: dict) -> dict:
    """Generate the answer key PDF for a collection."""
    collection = db.query(Collection).filter(Collection.id == collection_id).first()
    if not collection:
        raise ValueError(f"collection {collection_id} not found")

    questions = (
        db.query(Question)
        .join(Document, Question.document_id == Document.id)
        .filter(Document.collection_id == collection_id)
        .all()
    )
    groups = _group_questions(questions, config.get("group_by", "part"))

    answer_map: dict[int, dict] = {}
    for q in questions:
        ans = db.query(Answer).filter(Answer.question_id == q.id, Answer.status == "generated").first()
        if ans:
            answer_map[q.id] = ans.content

    processed = [d for d in collection.documents if d.status == "done"]
    subject_name = config.get("subject_name") or _first_nonempty(processed, "subject_name")
    subject_code = config.get("subject_code") or _first_nonempty(processed, "subject_code")
    semester = config.get("semester") or _first_nonempty(processed, "semester")
    degree = config.get("degree") or _first_nonempty(processed, "degree")
    years = sorted({d.year for d in processed if d.year})

    wm_img, _ = _resolve_watermark(config)

    total_q = sum(len(g["questions"]) for g in groups)
    answered = sum(1 for g in groups for q in g["questions"] if q.get("id") in answer_map)

    context = {
        "logo_url": _file_url_or_none("assets/studique-logo.png"),
        "meta": {
            "bank_title": config.get("bank_title") or "ANSWER KEY",
            "subject_name": subject_name or "",
            "subject_code": subject_code or "",
            "course": config.get("course") or (degree or ""),
            "semester": semester or "",
            "coverage": config.get("coverage") or "All Units",
            "years": years,
            "compiled_on": datetime.now(timezone.utc).strftime("%d %B %Y"),
            "answered_count": answered,
            "total_questions": total_q,
            "notes": config.get("notes") or "",
        },
        "groups": groups,
        "answer_map": answer_map,
        "watermark": config.get("watermark") or {},
        "watermark_img": wm_img,
    }

    css = (TEMPLATES_DIR / "answerkey" / "styles.css").read_text(encoding="utf-8")
    env = document._env
    template = env.get_template("answerkey/template.html.j2")
    html = template.render(css=css, **context)
    html = html.replace("@page {", "@page { size: A4;", 1)
    # Fix MathJax path: replace relative ../mathjax/ with absolute file:// URI
    mathjax_dir = (TEMPLATES_DIR / "mathjax").resolve().as_uri()
    html = html.replace("../mathjax/", mathjax_dir + "/")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = config.get("bank_title") or "Answer_Key"
    out_key = f"banks/answerkey_{collection_id}_{stamp}.pdf"
    out_path = storage.path(out_key)

    subject = f"{subject_name} {subject_code or ''}".strip()
    document.generate_pdf(html, out_path, logo_url=context.get("logo_url"), subject=subject)

    gen = QbGeneration(
        collection_id=collection_id,
        name=name,
        template="answerkey",
        config=config,
        output_key=out_key,
        question_count=total_q,
    )
    db.add(gen)
    db.commit()
    return {"generation_id": gen.id, "output_key": out_key, "name": name, "question_count": total_q}


def _first_nonempty(items: list, field: str):
    for item in items:
        v = getattr(item, field, None)
        if v:
            return v
    return None
