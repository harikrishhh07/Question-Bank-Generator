import shutil
import time
from pathlib import Path

from ..config import settings
from ..db import SessionLocal
from ..models import Collection, Document, Job
from ..storage import storage
from . import pipeline


def _process_document_job(db, job: Job) -> None:
    doc_id = job.payload.get("document_id")
    document = db.query(Document).filter(Document.id == doc_id).first()
    if not document:
        job.status = "error"
        job.error = "document not found"
        return

    document.status = "processing"
    src = storage.path(document.storage_key)
    result = pipeline.process_document(db, document, src)
    job.result = result
    job.status = "done"
    document.status = result.get("status", "done")


def _generate_bank_job(db, job: Job) -> None:
    from ..generation.document import generate_bank

    collection_id = job.payload.get("collection_id")
    config = job.payload.get("config") or {}
    result = generate_bank(db, collection_id, config)
    job.result = {"generation_id": result["generation_id"], "output_key": result.get("output_key")}
    job.status = "done"


def _generate_answers_job(db, job: Job) -> None:
    from ..answers import generate_answers_for_collection

    collection_id = job.payload.get("collection_id")
    result = generate_answers_for_collection(db, collection_id)
    job.result = result
    job.status = "done"


def _generate_answerkey_job(db, job: Job) -> None:
    from ..generation.answerkey import generate_answerkey

    collection_id = job.payload.get("collection_id")
    config = job.payload.get("config") or {}
    result = generate_answerkey(db, collection_id, config)
    job.result = {"generation_id": result["generation_id"], "output_key": result.get("output_key")}
    job.status = "done"


def run_job(job: Job) -> None:
    handlers = {
        "process_document": _process_document_job,
        "generate_bank": _generate_bank_job,
        "generate_answers": _generate_answers_job,
        "generate_answerkey": _generate_answerkey_job,
    }
    handler = handlers.get(job.type)
    if not handler:
        job.status = "error"
        job.error = f"unknown job type {job.type}"
        return
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job.id).first()
        job.status = "running"
        db.commit()
        handler(db, job)
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(Job).filter(Job.id == job.id).first()
        if job:
            job.status = "error"
            job.error = str(exc)
            db.commit()
    finally:
        db.close()


def worker_loop(once: bool = False, poll_seconds: float = 2.0) -> None:
    db = SessionLocal()
    try:
        while True:
            job = (
                db.query(Job)
                .filter(Job.status.in_(["queued", "running"]))
                .order_by(Job.id.asc())
                .first()
            )
            if job and job.status == "queued":
                db.expunge(job)
                run_job(job)
            elif not job:
                if once:
                    break
                time.sleep(poll_seconds)
            else:
                time.sleep(poll_seconds)
    finally:
        db.close()


if __name__ == "__main__":
    worker_loop()
