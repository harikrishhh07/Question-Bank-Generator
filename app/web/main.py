from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session, init_db
from ..models import Collection, Document, Job, MediaItem, Page, QbGeneration, Question, QuestionVersion
from ..models import Answer
from ..pipeline.job_runner import worker_loop
from ..storage import storage

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"

_worker_thread: threading.Thread | None = None


@app.on_event("startup")
def _startup() -> None:
    init_db()
    _seed_assets()
    _reset_stuck_jobs()
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=worker_loop, args=(False, 1.0), daemon=True)
        _worker_thread.start()


def _reset_stuck_jobs() -> None:
    """Reset any jobs stuck in 'running' state from a previous crashed session."""
    from ..db import SessionLocal as _SL
    try:
        db = _SL()
        stuck = db.query(Job).filter(Job.status == "running").all()
        for j in stuck:
            j.status = "queued"
            j.error = None
        if stuck:
            db.commit()
        db.close()
    except Exception:
        pass


def _seed_assets() -> None:
    logo = Path(settings.data_path) / "files" / "assets" / "studique-logo.png"
    if not logo.exists():
        src = Path(__file__).resolve().parent.parent.parent / "assets" / "studique-logo.png"
        if src.exists():
            logo.parent.mkdir(parents=True, exist_ok=True)
            logo.write_bytes(src.read_bytes())


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
@app.get("/files/{key:path}")
def serve_file(key: str):
    if not storage.exists(key):
        raise HTTPException(404, "not found")
    return FileResponse(storage.path(key))


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------
@app.get("/api/collections")
def list_collections(db: Session = Depends(get_session)):
    cols = db.query(Collection).order_by(Collection.id.desc()).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "created_at": c.created_at.isoformat(),
            "document_count": len(c.documents),
        }
        for c in cols
    ]


@app.post("/api/collections")
def create_collection(payload: dict, db: Session = Depends(get_session)):
    c = Collection(name=payload.get("name") or "New Collection")
    db.add(c)
    db.commit()
    return {"id": c.id, "name": c.name}


@app.delete("/api/collections/{cid}")
def delete_collection(cid: int, db: Session = Depends(get_session)):
    c = db.query(Collection).filter(Collection.id == cid).first()
    if not c:
        raise HTTPException(404)
    # Explicit ordered delete to avoid FK cascade deadlocks with worker thread
    for doc in list(c.documents):
        _delete_document_cascade(db, doc.id)
    db.query(Collection).filter(Collection.id == cid).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


@app.get("/api/collections/{cid}")
def collection_detail(cid: int, db: Session = Depends(get_session)):
    c = db.query(Collection).filter(Collection.id == cid).first()
    if not c:
        raise HTTPException(404)
    docs = []
    for d in c.documents:
        docs.append(
            {
                "id": d.id,
                "filename": d.filename,
                "status": d.status,
                "subject_code": d.subject_code,
                "subject_name": d.subject_name,
                "exam_session": d.exam_session,
                "year": d.year,
                "page_count": d.page_count,
                "error": d.error,
                "question_count": len(d.questions),
            }
        )
    return {"id": c.id, "name": c.name, "documents": docs}


# ---------------------------------------------------------------------------
# documents & upload
# ---------------------------------------------------------------------------
@app.post("/api/collections/{cid}/documents")
async def upload_documents(cid: int, files: list[UploadFile] = File(...), db: Session = Depends(get_session)):
    c = db.query(Collection).filter(Collection.id == cid).first()
    if not c:
        raise HTTPException(404, "collection not found")

    created = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        key = f"source/{uuid.uuid4().hex}.pdf"
        storage.put_bytes(key, data)
        doc = Document(collection_id=cid, filename=f.filename or "unnamed.pdf", storage_key=key)
        db.add(doc)
        db.flush()
        job = Job(type="process_document", payload={"document_id": doc.id})
        db.add(job)
        created.append({"document_id": doc.id, "filename": doc.filename})
    db.commit()
    return {"uploaded": created}


def _delete_document_cascade(db: Session, did: int) -> None:
    """Delete a document and all its child rows in safe dependency order."""
    q_ids = db.query(Question.id).filter(Question.document_id == did).subquery()
    db.query(QuestionVersion).filter(QuestionVersion.question_id.in_(q_ids)).delete(synchronize_session=False)
    db.query(Answer).filter(Answer.question_id.in_(q_ids)).delete(synchronize_session=False)
    db.query(Question).filter(Question.document_id == did).delete(synchronize_session=False)
    db.query(MediaItem).filter(MediaItem.document_id == did).delete(synchronize_session=False)
    db.query(Page).filter(Page.document_id == did).delete(synchronize_session=False)
    db.query(Document).filter(Document.id == did).delete(synchronize_session=False)
    db.flush()


@app.delete("/api/documents/{did}")
def delete_document(did: int, db: Session = Depends(get_session)):
    d = db.query(Document).filter(Document.id == did).first()
    if not d:
        raise HTTPException(404)
    _delete_document_cascade(db, did)
    db.commit()
    return {"ok": True}


@app.post("/api/documents/{did}/reprocess")
def reprocess_document(did: int, db: Session = Depends(get_session)):
    d = db.query(Document).filter(Document.id == did).first()
    if not d:
        raise HTTPException(404)
    job = Job(type="process_document", payload={"document_id": did})
    db.add(job)
    d.status = "pending"
    db.commit()
    return {"ok": True, "job_id": job.id}


@app.get("/api/documents/{did}")
def document_detail(did: int, db: Session = Depends(get_session)):
    d = db.query(Document).filter(Document.id == did).first()
    if not d:
        raise HTTPException(404)

    pages = [
        {
            "page_number": p.page_number,
            "width": p.width,
            "height": p.height,
            "render_url": storage.url(p.render_key),
            "has_text_layer": p.has_text_layer,
        }
        for p in sorted(d.pages, key=lambda p: p.page_number)
    ]

    media = [
        {
            "id": m.id,
            "type": m.media_type,
            "thumb_url": storage.url(m.thumb_key or m.orig_key),
            "orig_url": storage.url(m.orig_key),
            "caption": m.caption,
            "shared": m.shared,
            "page": m.page_id,
        }
        for m in d.media
    ]

    questions = []
    for q in sorted(d.questions, key=lambda x: (x.part or "", _qnum(x.number))):
        questions.append(
            {
                "id": q.id,
                "part": q.part,
                "number": q.number,
                "numbering": q.numbering,
                "marks": q.marks,
                "text": q.text,
                "options": q.options,
                "subs": q.subs,
                "media_ids": q.media_ids,
                "media_refs": q.media_refs,
                "page_ids": q.page_ids,
                "confidence": q.confidence,
                "flags": q.flags,
                "status": q.status,
                "version": q.version,
            }
        )

    return {
        "id": d.id,
        "filename": d.filename,
        "status": d.status,
        "subject_code": d.subject_code,
        "subject_name": d.subject_name,
        "exam_session": d.exam_session,
        "semester": d.semester,
        "year": d.year,
        "time_limit": d.time_limit,
        "max_marks": d.max_marks,
        "degree": d.degree,
        "error": d.error,
        "warnings": d.meta.get("warnings", []) if d.meta else [],
        "pages": pages,
        "media": media,
        "questions": questions,
    }


def _qnum(number):
    try:
        return int(str(number).split(".")[0])
    except ValueError:
        return 10**9


# ---------------------------------------------------------------------------
# question editing / review
# ---------------------------------------------------------------------------
@app.post("/api/questions/{qid}")
def update_question(qid: int, payload: dict, db: Session = Depends(get_session)):
    q = db.query(Question).filter(Question.id == qid).first()
    if not q:
        raise HTTPException(404)

    snapshot = {
        "text": q.text,
        "marks": q.marks,
        "number": q.number,
        "part": q.part,
        "options": q.options,
        "subs": q.subs,
        "media_ids": q.media_ids,
        "status": q.status,
    }
    db.add(QuestionVersion(question_id=q.id, snapshot=snapshot, reason=payload.get("reason", "edit")))

    for field in ["text", "number", "part", "status", "numbering"]:
        if field in payload:
            setattr(q, field, payload[field])
    if "marks" in payload:
        q.marks = payload.get("marks")
    if "options" in payload:
        q.options = payload.get("options") or []
    if "subs" in payload:
        q.subs = payload.get("subs") or []
    if "media_ids" in payload:
        q.media_ids = payload.get("media_ids") or []
    if "confidence" in payload:
        q.confidence = payload.get("confidence")

    q.version += 1
    db.commit()
    return {"ok": True, "id": q.id, "version": q.version}


@app.post("/api/questions/{qid}/status")
def set_question_status(qid: int, payload: dict, db: Session = Depends(get_session)):
    q = db.query(Question).filter(Question.id == qid).first()
    if not q:
        raise HTTPException(404)
    status = payload.get("status")
    if status not in {"approved", "review", "rejected"}:
        raise HTTPException(400, "invalid status")
    db.add(QuestionVersion(question_id=q.id, snapshot={"status": q.status}, reason=f"status -> {status}"))
    q.status = status
    q.version += 1
    db.commit()
    return {"ok": True}


@app.delete("/api/questions/{qid}")
def delete_question(qid: int, db: Session = Depends(get_session)):
    q = db.query(Question).filter(Question.id == qid).first()
    if not q:
        raise HTTPException(404)
    db.delete(q)
    db.commit()
    return {"ok": True}


@app.post("/api/documents/{did}/questions")
def add_question(did: int, payload: dict, db: Session = Depends(get_session)):
    d = db.query(Document).filter(Document.id == did).first()
    if not d:
        raise HTTPException(404)
    q = Question(
        document_id=did,
        part=payload.get("part", ""),
        number=payload.get("number", ""),
        numbering=payload.get("numbering", payload.get("number", "")),
        marks=payload.get("marks"),
        text=payload.get("text", ""),
        options=payload.get("options", []),
        subs=payload.get("subs", []),
        media_ids=payload.get("media_ids", []),
        page_ids=payload.get("page_ids", []),
        confidence={"overall": 1.0},
        status="approved",
    )
    db.add(q)
    db.commit()
    return {"ok": True, "id": q.id}


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------
@app.post("/api/collections/{cid}/generate")
def generate(cid: int, payload: dict, db: Session = Depends(get_session)):
    job = Job(type="generate_bank", payload={"collection_id": cid, "config": payload})
    db.add(job)
    db.commit()
    return {"ok": True, "job_id": job.id}


@app.get("/api/banks")
def list_banks(db: Session = Depends(get_session)):
    gens = db.query(QbGeneration).order_by(QbGeneration.id.desc()).limit(50).all()
    return [
        {
            "id": g.id,
            "name": g.name,
            "collection_id": g.collection_id,
            "template": g.template,
            "question_count": g.question_count,
            "created_at": g.created_at.isoformat(),
            "url": storage.url(g.output_key) if g.output_key else None,
        }
        for g in gens
    ]


@app.post("/api/collections/{cid}/answers/generate")
def generate_answers(cid: int, db: Session = Depends(get_session)):
    job = Job(type="generate_answers", payload={"collection_id": cid})
    db.add(job)
    db.commit()
    return {"ok": True, "job_id": job.id}


@app.post("/api/collections/{cid}/answerkey/generate")
def generate_answerkey(cid: int, payload: dict = None, db: Session = Depends(get_session)):
    job = Job(type="generate_answerkey", payload={"collection_id": cid, "config": payload or {}})
    db.add(job)
    db.commit()
    return {"ok": True, "job_id": job.id}


@app.get("/api/answers/{cid}")
def collection_answers(cid: int, db: Session = Depends(get_session)):
    """Answers for all questions in a collection (with the bank numbering)."""
    from ..generation.context import _group_questions

    questions = (
        db.query(Question)
        .join(Document, Question.document_id == Document.id)
        .filter(Document.collection_id == cid)
        .all()
    )
    groups = _group_questions(questions, "part")
    answer_map = {}
    for a in db.query(Answer).filter(Answer.status == "generated").all():
        answer_map[a.question_id] = a

    out = []
    for g in groups:
        for q in g["questions"]:
            ans = answer_map.get(q["id"])
            out.append(
                {
                    "id": q["id"],
                    "number": q["numbering"],
                    "part": q["part"],
                    "question": q["text"],
                    "status": ans.status if ans else "pending",
                    "content": ans.content if ans and ans.status == "generated" else None,
                    "error": ans.error if ans else None,
                }
            )
    return {"questions": out}


@app.post("/api/questions/{qid}/answer")
def update_answer(qid: int, payload: dict, db: Session = Depends(get_session)):
    """Manually set / edit the answer for a question."""
    q = db.query(Question).filter(Question.id == qid).first()
    if not q:
        raise HTTPException(404)
    ans = db.query(Answer).filter(Answer.question_id == qid).first()
    if ans is None:
        ans = Answer(question_id=qid)
        db.add(ans)
    ans.content = payload.get("content") or ans.content
    ans.status = "generated"
    ans.error = None
    db.commit()
    return {"ok": True, "id": ans.id}


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_session)):
    jobs = db.query(Job).order_by(Job.id.desc()).limit(30).all()
    return [
        {
            "id": j.id,
            "type": j.type,
            "status": j.status,
            "error": j.error,
            "created_at": j.created_at.isoformat(),
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            "result": j.result,
        }
        for j in jobs
    ]


# ---------------------------------------------------------------------------
# media
# ---------------------------------------------------------------------------
@app.get("/api/media")
def list_media(db: Session = Depends(get_session)):
    items = db.query(MediaItem).order_by(MediaItem.id.desc()).limit(100).all()
    return [
        {
            "id": m.id,
            "document_id": m.document_id,
            "type": m.media_type,
            "thumb_url": storage.url(m.thumb_key or m.orig_key),
            "orig_url": storage.url(m.orig_key),
            "caption": m.caption,
            "shared": m.shared,
        }
        for m in items
    ]


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
@app.get("/")
def ui():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
