"""
Pytest setup for OBSYD backend.

Each test gets a fresh in-memory SQLite database so tests don't share
state and don't touch the production WAL file. The backend's `engine`
and `SessionLocal` are monkey-patched to point at the per-test engine
before any code-under-test imports them.
"""

import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `import backend.*` works when pytest
# is invoked from anywhere.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Set the secrets BEFORE any backend.config import, so pydantic_settings
# doesn't complain about missing required values.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-32chars-min")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-use-32chars-min")
os.environ.setdefault("AISSTREAM_API_KEY", "")
os.environ.setdefault("EIA_API_KEY", "")
os.environ.setdefault("FRED_API_KEY", "")
os.environ.setdefault("LEMONSQUEEZY_WEBHOOK_SECRET", "test-lemonsqueezy-webhook-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.database as _db_module
from backend.database import Base


# Modules that did `from backend.database import SessionLocal` at import time
# and therefore hold their own binding. Every test that hits the DB must
# rebind `SessionLocal` in each of these namespaces too.
_DB_CONSUMERS = (
    "backend.auth.dependencies",
    "backend.routes.auth",
    "backend.routes.webhooks",
)


@pytest.fixture
def db_session(monkeypatch):
    """Per-test in-memory SQLite engine + session. Fully isolated."""
    # StaticPool is critical: SQLite ":memory:" databases are per-connection,
    # so without it each new SessionLocal() gets an empty DB and tests see
    # "no such table". StaticPool reuses one connection across all sessions.
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Rebind in the database module + in every consumer that already
    # imported SessionLocal at module load time.
    monkeypatch.setattr(_db_module, "engine", test_engine)
    monkeypatch.setattr(_db_module, "SessionLocal", TestSessionLocal)
    for mod_path in _DB_CONSUMERS:
        import importlib

        try:
            mod = importlib.import_module(mod_path)
        except ImportError:
            continue
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", TestSessionLocal)

    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        test_engine.dispose()
