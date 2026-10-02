from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="Untitled")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    documents: Mapped[list["Document"]] = relationship(back_populates="collection", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str] = mapped_column(String(500))

    subject_code: Mapped[Optional[str]] = mapped_column(String(100))
    subject_name: Mapped[Optional[str]] = mapped_column(String(300))
    exam_session: Mapped[Optional[str]] = mapped_column(String(100))
    semester: Mapped[Optional[str]] = mapped_column(String(100))
    year: Mapped[Optional[int]] = mapped_column(Integer)
    time_limit: Mapped[Optional[str]] = mapped_column(String(50))
    max_marks: Mapped[Optional[str]] = mapped_column(String(50))
    degree: Mapped[Optional[str]] = mapped_column(String(200))

    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending|processing|done|error
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    collection: Mapped["Collection"] = relationship(back_populates="documents")
    pages: Mapped[list["Page"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    questions: Mapped[list["Question"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    media: Mapped[list["MediaItem"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    render_key: Mapped[str] = mapped_column(String(500))
    has_text_layer: Mapped[bool] = mapped_column(default=False)
    text_layer: Mapped[Optional[str]] = mapped_column(Text)
    ocr_conf: Mapped[float] = mapped_column(Float, default=0.0)

    document: Mapped["Document"] = relationship(back_populates="pages")


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    media_type: Mapped[str] = mapped_column(String(50))  # figure|table|equation|image
    bbox: Mapped[list] = mapped_column(JSON)  # [x0,y0,x1,y1] normalized 0-1
    orig_key: Mapped[str] = mapped_column(String(500))
    thumb_key: Mapped[Optional[str]] = mapped_column(String(500))
    caption: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    shared: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    document: Mapped["Document"] = relationship(back_populates="media")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    page_ids: Mapped[list] = mapped_column(JSON, default=list)

    part: Mapped[Optional[str]] = mapped_column(String(20))
    number: Mapped[Optional[str]] = mapped_column(String(50))
    numbering: Mapped[Optional[str]] = mapped_column(String(50))
    title: Mapped[Optional[str]] = mapped_column(String(300))
    marks: Mapped[Optional[int]] = mapped_column(Integer)

    text: Mapped[Optional[str]] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)  # MCQ options
    subs: Mapped[list] = mapped_column(JSON, default=list)  # subquestions
    media_ids: Mapped[list] = mapped_column(JSON, default=list)
    media_refs: Mapped[list] = mapped_column(JSON, default=list)

    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[dict] = mapped_column(JSON, default=dict)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|review|approved|rejected
    version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    document: Mapped["Document"] = relationship(back_populates="questions")
    versions: Mapped[list["QuestionVersion"]] = relationship(back_populates="question", cascade="all, delete-orphan")
    answers: Mapped[list["Answer"]] = relationship(back_populates="question", cascade="all, delete-orphan")


class QuestionVersion(Base):
    __tablename__ = "question_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    question: Mapped["Question"] = relationship(back_populates="versions")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="queued")  # queued|running|done|error
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class QbGeneration(Base):
    __tablename__ = "qb_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    template: Mapped[str] = mapped_column(String(100), default="standard")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    output_key: Mapped[Optional[str]] = mapped_column(String(500))
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True, unique=True)
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # {steps: [...], final_answer: "...", correct_option: "A"}
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|generated|error
    error: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    question: Mapped["Question"] = relationship(back_populates="answers")


class AnswerGeneration(Base):
    """Tracks a batch answer-generation job per collection."""
    __tablename__ = "answer_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|running|done|error
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
