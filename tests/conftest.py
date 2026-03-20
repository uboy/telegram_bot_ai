"""
Pytest fixtures for RAG Index Lifecycle tests.
"""
import os
import pytest
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import Base, KnowledgeBase, KnowledgeChunk, Document
from shared import cache as _cache_module


# Use SQLite for tests to avoid MySQL dependency
TEST_DB_PATH = "data/test_rag_index_lifecycle.db"
os.makedirs(os.path.dirname(TEST_DB_PATH), exist_ok=True)

_test_engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
_TestSession = sessionmaker(bind=_test_engine)


@contextmanager
def _test_db_session():
    """Context manager that provides a test DB session with automatic cleanup."""
    # Create tables if they don't exist
    Base.metadata.create_all(bind=_test_engine)
    
    session = _TestSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def test_db_session():
    """Provide a test DB session with automatic cleanup."""
    with _test_db_session() as session:
        yield session


@pytest.fixture(autouse=True)
def cleanup_test_db():
    """Cleanup test database and semantic cache after each test."""
    # Clear semantic cache before each test to prevent cross-test pollution (RAGPERF-002)
    if _cache_module.rag_cache is not None:
        with _cache_module.rag_cache._lock:
            _cache_module.rag_cache._cache.clear()
    yield
    # Drop all tables after test
    Base.metadata.drop_all(bind=_test_engine)
    # Clear semantic cache after test
    if _cache_module.rag_cache is not None:
        with _cache_module.rag_cache._lock:
            _cache_module.rag_cache._cache.clear()
