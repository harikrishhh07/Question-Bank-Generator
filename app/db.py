from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _engine_url() -> str:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return url


# Use NullPool for SQLite: one connection per request/thread, no pool sharing.
# This is the safest option for multi-threaded FastAPI + background worker.
from sqlalchemy.pool import NullPool, StaticPool

_IS_SQLITE = settings.database_url.startswith("sqlite")

engine = create_engine(
    _engine_url(),
    connect_args={"check_same_thread": False, "timeout": 20} if _IS_SQLITE else {},
    poolclass=NullPool if _IS_SQLITE else None,
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record):
    """Safe pragmas for concurrent access. Called once per new connection."""
    if not _IS_SQLITE:
        return
    cur = dbapi_conn.cursor()
    try:
        # WAL allows concurrent reads + one writer without locking
        cur.execute("PRAGMA journal_mode=WAL")
        # Wait up to 20s instead of failing immediately on a locked DB
        cur.execute("PRAGMA busy_timeout=20000")
        # NORMAL is crash-safe and faster than FULL for our use case
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA cache_size=-4000")   # 4MB page cache per connection
    finally:
        cur.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
