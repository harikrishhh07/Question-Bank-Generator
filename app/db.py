from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _engine_url() -> str:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        if not Path(path).is_absolute():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return url


engine = create_engine(
    _engine_url(),
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
