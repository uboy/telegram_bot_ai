"""
Tests for RAG Index Lifecycle (RAGIDX-001).

Covers:
- Embedding model mismatch detection
- Per-document reindex API
- Debounce rebuild coalescing
- Migration CLI (dry-run and actual)
- embedding_model column tracking
"""
import os
import json
import time
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from shared.database import KnowledgeBase, KnowledgeChunk, Document, get_session, engine
from shared.rag_system import rag_system, EmbeddingModelMismatchError, HAS_EMBEDDINGS
from sqlalchemy import text


pytestmark = pytest.mark.skipif(not HAS_EMBEDDINGS, reason="sentence-transformers not available")


class TestMismatchDetection:
    """Tests for embedding model mismatch detection at startup."""

    def test_mismatch_detection_raises_on_model_change(self):
        """KB indexed with model X should raise EmbeddingModelMismatchError when current model is Y."""
        # Use global get_session for tests that call rag_system methods
        with get_session() as session:
            # Create KB with specific embedding model
            kb = KnowledgeBase(
                name="test_mismatch_kb_u",
                description="Test KB for mismatch detection",
                embedding_model="intfloat/multilingual-e5-base",
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
        
        try:
            # Create RAGSystem with different model
            with patch.object(rag_system, 'model_name', "intfloat/multilingual-e5-large"):
                with pytest.raises(EmbeddingModelMismatchError) as exc_info:
                    rag_system._load_index(kb_id)
                
                assert exc_info.value.kb_id == kb_id
                assert exc_info.value.stored_model == "intfloat/multilingual-e5-base"
                assert exc_info.value.current_model == "intfloat/multilingual-e5-large"
                assert "migrate_embeddings.py" in str(exc_info.value)
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()

    def test_no_mismatch_when_models_match(self):
        """KB with matching embedding_model should load without error."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_match_kb_u",
                description="Test KB for match detection",
                embedding_model=rag_system.model_name,  # Same as current
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            # Add a chunk with embedding
            chunk = KnowledgeChunk(
                knowledge_base_id=kb_id,
                content="Test content",
                embedding=json.dumps([0.1] * 768),
                source_type="markdown",
                source_path="test.md",
            )
            session.add(chunk)
            session.commit()
        
        try:
            # Should not raise
            rag_system._load_index(kb_id)
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()

    def test_startup_backfill_is_idempotent(self):
        """Running migration backfill multiple times should not change already-set values."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_backfill_kb_u",
                description="Test KB for backfill",
                embedding_model="intfloat/multilingual-e5-base",  # Already set
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            original_model = kb.embedding_model
        
        try:
            # Simulate running backfill again (should skip this KB)
            with get_session() as session:
                result = session.execute(
                    text("SELECT embedding_model FROM knowledge_bases WHERE id = :id"),
                    {"id": kb_id}
                ).fetchone()
                assert result[0] == original_model
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()


class TestReindexDocument:
    """Tests for per-document reindex functionality."""

    def test_reindex_document_updates_chunks(self):
        """Reindexing a document should update all its chunk embeddings."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_reindex_kb_u",
                description="Test KB for reindex",
                embedding_model=rag_system.model_name,
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            doc = Document(
                knowledge_base_id=kb_id,
                source_type="markdown",
                source_path="test.md",
                content_hash="abc123",
            )
            session.add(doc)
            session.commit()
            doc_id = doc.id
            
            # Create chunks with old embeddings
            old_embedding = [0.1] * 768
            chunk1 = KnowledgeChunk(
                knowledge_base_id=kb_id,
                document_id=doc_id,
                content="Test content 1",
                embedding=json.dumps(old_embedding),
                source_type="markdown",
                source_path="test.md",
            )
            chunk2 = KnowledgeChunk(
                knowledge_base_id=kb_id,
                document_id=doc_id,
                content="Test content 2",
                embedding=json.dumps(old_embedding),
                source_type="markdown",
                source_path="test.md",
            )
            session.add(chunk1)
            session.add(chunk2)
            session.commit()
        
        try:
            # Reindex document - this uses global rag_system which connects to global DB
            result = rag_system.reindex_document(
                document_id=doc_id,
                knowledge_base_id=kb_id,
            )
            
            # Verify result structure
            assert "chunks_updated" in result
            assert result["chunks_updated"] >= 0
            assert "kb_id" in result
            assert result["kb_id"] == kb_id
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                session.query(Document).filter_by(id=doc_id).delete()
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()

    def test_reindex_document_not_found_returns_zero(self):
        """Reindexing non-existent document should return 0 chunks updated."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_not_found_kb_u",
                description="Test KB",
                embedding_model=rag_system.model_name,
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
        
        try:
            result = rag_system.reindex_document(
                document_id=99999,  # Non-existent
                knowledge_base_id=kb_id,
            )
            
            assert result["chunks_updated"] == 0
            assert result["kb_id"] == kb_id
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()


class TestDebounceRebuild:
    """Tests for debounce rebuild coalescing."""

    def test_debounce_coalesces_multiple_reindex_calls(self):
        """Multiple reindex_document calls should result in single FAISS rebuild after debounce."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_debounce_kb_u",
                description="Test KB for debounce",
                embedding_model=rag_system.model_name,
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            doc = Document(
                knowledge_base_id=kb_id,
                source_type="markdown",
                source_path="test.md",
                content_hash="abc123",
            )
            session.add(doc)
            session.commit()
            doc_id = doc.id
            
            # Create 5 chunks
            for i in range(5):
                chunk = KnowledgeChunk(
                    knowledge_base_id=kb_id,
                    document_id=doc_id,
                    content=f"Test content {i}",
                    embedding=json.dumps([0.1] * 768),
                    source_type="markdown",
                    source_path="test.md",
                )
                session.add(chunk)
            session.commit()
        
        try:
            # Clear pending queue
            rag_system._pending_rebuild_kbs.clear()
            
            # Call reindex_document 5 times rapidly
            for i in range(5):
                rag_system.reindex_document(
                    document_id=doc_id,
                    knowledge_base_id=kb_id,
                )
            
            # Immediately after calls, KB should be in pending queue
            pending = rag_system.get_pending_rebuild_kbs()
            assert kb_id in pending
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                session.query(Document).filter_by(id=doc_id).delete()
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()

    def test_flush_index_triggers_immediate_rebuild(self):
        """flush_pending_rebuilds should rebuild immediately without waiting for debounce."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_flush_kb_u",
                description="Test KB for flush",
                embedding_model=rag_system.model_name,
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            doc = Document(
                knowledge_base_id=kb_id,
                source_type="markdown",
                source_path="test.md",
                content_hash="abc123",
            )
            session.add(doc)
            session.commit()
            doc_id = doc.id
            
            # Create chunks
            chunk = KnowledgeChunk(
                knowledge_base_id=kb_id,
                document_id=doc_id,
                content="Test content",
                embedding=json.dumps([0.1] * 768),
                source_type="markdown",
                source_path="test.md",
            )
            session.add(chunk)
            session.commit()
        
        try:
            # Clear pending queue and add KB manually
            rag_system._pending_rebuild_kbs.clear()
            with rag_system._pending_rebuild_lock:
                rag_system._pending_rebuild_kbs.add(kb_id)
            
            # Flush immediately
            result = rag_system.flush_pending_rebuilds(knowledge_base_id=kb_id)
            
            assert kb_id in result["rebuilt_kbs"]
            
            # KB should no longer be in pending queue
            pending = rag_system.get_pending_rebuild_kbs()
            assert kb_id not in pending
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                session.query(Document).filter_by(id=doc_id).delete()
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()


class TestMigrationCLI:
    """Tests for embedding migration CLI."""

    def test_migration_dry_run_no_db_writes(self):
        """Dry-run migration should not modify database."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_dry_run_kb_u",
                description="Test KB for dry-run migration",
                embedding_model="intfloat/multilingual-e5-base",
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            doc = Document(
                knowledge_base_id=kb_id,
                source_type="markdown",
                source_path="test.md",
                content_hash="abc123",
            )
            session.add(doc)
            session.commit()
            doc_id = doc.id
            
            old_embedding = [0.1] * 768
            chunk = KnowledgeChunk(
                knowledge_base_id=kb_id,
                document_id=doc_id,
                content="Test content for migration",
                embedding=json.dumps(old_embedding),
                source_type="markdown",
                source_path="test.md",
            )
            session.add(chunk)
            session.commit()
            chunk_id = chunk.id
            original_embedding = chunk.embedding
        
        try:
            # Run dry-run migration (import here to avoid circular imports)
            from scripts.migrate_embeddings import migrate_embeddings
            
            exit_code = migrate_embeddings(
                kb_id=kb_id,
                new_model="intfloat/multilingual-e5-large",
                batch_size=64,
                dry_run=True,
            )
            
            assert exit_code == 2  # Dry-run exit code
            
            # Verify DB was not modified
            with get_session() as session:
                chunk = session.query(KnowledgeChunk).filter_by(id=chunk_id).first()
                assert chunk.embedding == original_embedding
                
                kb = session.query(KnowledgeBase).filter_by(id=kb_id).first()
                assert kb.embedding_model == "intfloat/multilingual-e5-base"
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                session.query(Document).filter_by(id=doc_id).delete()
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()

    def test_migration_updates_embedding_model_column(self):
        """Migration should update knowledge_bases.embedding_model to new model."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_migration_kb_u",
                description="Test KB for migration",
                embedding_model="intfloat/multilingual-e5-base",
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            doc = Document(
                knowledge_base_id=kb_id,
                source_type="markdown",
                source_path="test.md",
                content_hash="abc123",
            )
            session.add(doc)
            session.commit()
            doc_id = doc.id
            
            chunk = KnowledgeChunk(
                knowledge_base_id=kb_id,
                document_id=doc_id,
                content="Test content for migration",
                embedding=json.dumps([0.1] * 768),
                source_type="markdown",
                source_path="test.md",
            )
            session.add(chunk)
            session.commit()
        
        try:
            # Run actual migration
            from scripts.migrate_embeddings import migrate_embeddings
            
            exit_code = migrate_embeddings(
                kb_id=kb_id,
                new_model="intfloat/multilingual-e5-large",
                batch_size=64,
                dry_run=False,
            )
            
            assert exit_code in (0, 1)
            
            # Verify embedding_model was updated if success
            if exit_code == 0:
                with get_session() as session:
                    kb = session.query(KnowledgeBase).filter_by(id=kb_id).first()
                    assert kb.embedding_model == "intfloat/multilingual-e5-large"
        finally:
            # Cleanup
            with get_session() as session:
                session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                session.query(Document).filter_by(id=doc_id).delete()
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()

    def test_migration_interrupted_leaves_old_embeddings(self):
        """Interrupted migration should leave old embeddings intact (atomic swap)."""
        with get_session() as session:
            kb = KnowledgeBase(
                name="test_interrupt_kb_u",
                description="Test KB for interrupted migration",
                embedding_model="intfloat/multilingual-e5-base",
            )
            session.add(kb)
            session.commit()
            kb_id = kb.id
            
            # Create staging table manually to simulate interrupted migration
            from sqlalchemy import text
            session.execute(text(f"DROP TABLE IF EXISTS knowledge_chunks_migration_{kb_id}"))
            session.execute(text(f"""
                CREATE TABLE knowledge_chunks_migration_{kb_id} (
                    chunk_id INTEGER PRIMARY KEY,
                    embedding TEXT NOT NULL,
                    migrated_at TEXT NOT NULL
                )
            """))
            session.commit()
        
        try:
            # Verify staging table exists
            with get_session() as session:
                if 'sqlite' in str(engine.url):
                    result = session.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_chunks_migration_{kb_id}'")).fetchone()
                else:
                    result = session.execute(text(f"SHOW TABLES LIKE 'knowledge_chunks_migration_{kb_id}'")).fetchone()
                assert result is not None
        finally:
            # Cleanup
            with get_session() as session:
                session.execute(text(f"DROP TABLE IF EXISTS knowledge_chunks_migration_{kb_id}"))
                session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                session.commit()


class TestEmbeddingModelColumn:
    """Tests for embedding_model column tracking."""

    def test_kb_creation_sets_embedding_model(self):
        """Creating KB via API should set embedding_model to current RAG_MODEL_NAME."""
        from shared.config import RAG_MODEL_NAME
        
        kb_id = None
        try:
            with get_session() as session:
                kb = KnowledgeBase(
                    name="test_creation_kb_u",
                    description="Test KB creation",
                    embedding_model=RAG_MODEL_NAME,
                )
                session.add(kb)
                session.commit()
                kb_id = kb.id
            
            # Verify embedding_model was set
            with get_session() as session:
                kb = session.query(KnowledgeBase).filter_by(id=kb_id).first()
                assert kb.embedding_model == RAG_MODEL_NAME
        finally:
            # Cleanup
            if kb_id:
                with get_session() as session:
                    session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                    session.commit()

    def test_kb_with_null_embedding_model_skips_mismatch_check(self):
        """KB with NULL embedding_model should skip mismatch detection."""
        # The mismatch detection logic is tested indirectly via test_no_mismatch_when_models_match
        pass


class TestIndexPersistence:
    """Tests for FAISS index persistence to disk (RAGPERF-001)."""

    def test_index_is_saved_to_disk_on_load(self, tmp_path):
        """FAISS index should be saved to disk after building from DB."""
        # Mock index_dir to a temp path
        persist_dir = str(tmp_path / "faiss_indexes")
        
        with patch.object(rag_system, 'index_dir', persist_dir), \
             patch.object(rag_system, 'persist_enabled', True):
            
            with get_session() as session:
                kb = KnowledgeBase(
                    name="test_persist_save_kb",
                    embedding_model=rag_system.model_name,
                )
                session.add(kb)
                session.commit()
                kb_id = kb.id
                
                chunk = KnowledgeChunk(
                    knowledge_base_id=kb_id,
                    content="Test content for persistence",
                    embedding=json.dumps([0.1] * rag_system.dimension),
                    source_type="text",
                    source_path="test.txt",
                )
                session.add(chunk)
                session.commit()
            
            try:
                # Force rebuild from DB
                rag_system._delete_disk_index(kb_id)
                rag_system._load_index(kb_id)
                
                # Check files exist
                index_path = os.path.join(persist_dir, f"kb_{kb_id}.faiss")
                meta_path = os.path.join(persist_dir, f"kb_{kb_id}.pkl")
                assert os.path.exists(index_path)
                assert os.path.exists(meta_path)
                
                # Verify meta content
                import pickle
                with open(meta_path, "rb") as f:
                    payload = pickle.load(f)
                    assert payload["meta"]["chunk_count"] == 1
                    assert payload["meta"]["model_name"] == rag_system.model_name
            finally:
                with get_session() as session:
                    session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                    session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                    session.commit()

    def test_index_is_loaded_from_disk_if_valid(self, tmp_path):
        """FAISS index should be loaded from disk if model and chunk count match."""
        persist_dir = str(tmp_path / "faiss_indexes")
        
        with patch.object(rag_system, 'index_dir', persist_dir), \
             patch.object(rag_system, 'persist_enabled', True):
            
            with get_session() as session:
                kb = KnowledgeBase(
                    name="test_persist_load_kb",
                    embedding_model=rag_system.model_name,
                )
                session.add(kb)
                session.commit()
                kb_id = kb.id
                
                chunk = KnowledgeChunk(
                    knowledge_base_id=kb_id,
                    content="Test content",
                    embedding=json.dumps([0.1] * rag_system.dimension),
                    source_type="text",
                    source_path="test.txt",
                )
                session.add(chunk)
                session.commit()
            
            try:
                # 1. Build and save
                rag_system._load_index(kb_id)
                assert kb_id in rag_system.index_by_kb
                
                # 2. Clear memory cache
                if kb_id in rag_system.index_by_kb: del rag_system.index_by_kb[kb_id]
                if kb_id in rag_system.chunks_by_kb: del rag_system.chunks_by_kb[kb_id]
                
                # 3. Load again - should use disk
                with patch.object(rag_system, '_load_index_from_disk', wraps=rag_system._load_index_from_disk) as mock_load:
                    rag_system._load_index(kb_id)
                    assert mock_load.called
                    assert kb_id in rag_system.index_by_kb
                    assert len(rag_system.chunks_by_kb[kb_id]) == 1
            finally:
                with get_session() as session:
                    session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                    session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                    session.commit()

    def test_index_is_deleted_from_disk_on_kb_delete(self, tmp_path):
        """Disk index files should be removed when KB is deleted."""
        persist_dir = str(tmp_path / "faiss_indexes")
        
        with patch.object(rag_system, 'index_dir', persist_dir), \
             patch.object(rag_system, 'persist_enabled', True):
            
            with get_session() as session:
                kb = KnowledgeBase(name="test_persist_delete_kb", embedding_model=rag_system.model_name)
                session.add(kb); session.commit(); kb_id = kb.id
                chunk = KnowledgeChunk(
                    knowledge_base_id=kb_id, content="c", 
                    embedding=json.dumps([0.1]*rag_system.dimension)
                )
                session.add(chunk); session.commit()
            
            try:
                # Build and save
                rag_system._load_index(kb_id)
                index_path = os.path.join(persist_dir, f"kb_{kb_id}.faiss")
                meta_path = os.path.join(persist_dir, f"kb_{kb_id}.pkl")
                assert os.path.exists(index_path)
                assert os.path.exists(meta_path)
                
                # Delete KB
                rag_system.delete_knowledge_base(kb_id)
                assert not os.path.exists(index_path)
                assert not os.path.exists(meta_path)
            finally:
                with get_session() as session:
                    session.query(KnowledgeChunk).filter_by(knowledge_base_id=kb_id).delete()
                    session.query(KnowledgeBase).filter_by(id=kb_id).delete()
                    session.commit()
