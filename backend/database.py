from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.config import settings

# Size the pool to Starlette's ~40-thread pool: with the old 5+10 default a burst of
# sync handlers waited 30 s for a connection and 500'd ("QueuePool limit of size 5
# overflow 10 reached", 2026-11-11). SQLite/WAL connections are cheap and reads run
# concurrently, so the threadpool — not the pool — should be the bound. (In-memory
# SQLite uses SingletonThreadPool, which takes no sizing arguments.)
_pool_kwargs = {} if make_url(settings.database_url).database in (None, "", ":memory:") else {
    "pool_size": 10,
    "max_overflow": 30,
}

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30},  # SQLite-specific
    pool_pre_ping=True,
    **_pool_kwargs,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db():
    """Create all tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
