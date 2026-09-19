from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


@lru_cache
def engine():
    url = settings().database_url
    connect = (
        {"check_same_thread": False, "timeout": 10}
        if url.startswith("sqlite")
        else {"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=3000"}
    )
    eng = create_engine(url, pool_pre_ping=True, connect_args=connect)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def sqlite_settings(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    return eng


@contextmanager
def session_scope():
    with sessionmaker(bind=engine(), expire_on_commit=False)() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_db():
    with session_scope() as session:
        yield session


def row_dict(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
