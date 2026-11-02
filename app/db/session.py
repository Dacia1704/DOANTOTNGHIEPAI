"""Hai engine SQLAlchemy: schema chính (chỉ đọc) và schema rental_ai (đọc ghi)."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# Schema chính: chỉ SELECT. Bật pool_pre_ping để tránh connection chết.
core_engine = create_engine(
    _settings.core_db_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)

# Schema rental_ai: đọc ghi.
ai_engine = create_engine(
    _settings.ai_db_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)

core_session_factory = sessionmaker(bind=core_engine, autoflush=False, expire_on_commit=False)
ai_session_factory = sessionmaker(bind=ai_engine, autoflush=False, expire_on_commit=False)


def get_core_session() -> Iterator[Session]:
    """Dependency FastAPI: session chỉ đọc cho schema nghiệp vụ."""
    session = core_session_factory()
    try:
        yield session
    finally:
        session.close()


def get_ai_session() -> Iterator[Session]:
    """Dependency FastAPI: session đọc ghi cho schema rental_ai."""
    session = ai_session_factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def core_session() -> Iterator[Session]:
    with core_session_factory() as session:
        yield session


@contextmanager
def ai_session() -> Iterator[Session]:
    with ai_session_factory() as session:
        yield session
