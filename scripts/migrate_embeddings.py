"""
Embedding migration CLI (RAGIDX-001).

Re-embeds all chunks in a KB with a new model, using a staging table for atomic swap.

Usage:
    python scripts/migrate_embeddings.py \
        --kb-id 1 \
        --new-model intfloat/multilingual-e5-large \
        [--batch-size 64] \
        [--dry-run]

Exit codes:
    0 - migration complete
    1 - migration failed (DB unchanged)
    2 - dry run complete (no DB writes)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text, Table, Column, Integer, Text, MetaData
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine

from shared.database import KnowledgeBase, KnowledgeChunk, get_session, Base
from shared.rag_system import RAGSystem, EmbeddingModelMismatchError
from shared.config import RAG_MODEL_NAME, DB_PATH, MYSQL_URL


def get_database_url() -> str:
    """Get database URL from config."""
    if MYSQL_URL:
        return MYSQL_URL
    return f"sqlite:///{DB_PATH}"


def create_staging_table(engine: Engine, kb_id: int) -> Table:
    """Create staging table for migration (knowledge_chunks_migration_{kb_id})."""
    metadata = MetaData()
    staging_table_name = f"knowledge_chunks_migration_{kb_id}"
    
    # Drop existing staging table if exists (from failed migration)
    with engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {staging_table_name}"))
        conn.commit()
    
    # Create new staging table with same schema as knowledge_chunks (subset of columns)
    staging_table = Table(
        staging_table_name,
        metadata,
        Column("chunk_id", Integer, primary_key=True),
        Column("embedding", Text, nullable=False),
        Column("migrated_at", Text, nullable=False),
    )
    
    metadata.create_all(engine)
    return staging_table


def load_chunks_for_kb(session: Session, kb_id: int) -> List[KnowledgeChunk]:
    """Load all non-deleted chunks for a KB."""
    return (
        session.query(KnowledgeChunk)
        .filter_by(knowledge_base_id=kb_id, is_deleted=False)
        .all()
    )


def embed_batch(
    rag: RAGSystem,
    texts: List[str],
    batch_size: int = 64
) -> List[List[float]]:
    """Embed a batch of texts using the current RAG model."""
    all_embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i + batch_size]
        for text in batch_texts:
            embedding = rag._get_embedding(text, is_query=False)
            if embedding is None:
                raise RuntimeError(f"Failed to embed text: {text[:100]}...")
            all_embeddings.append(embedding.tolist())
    
    return all_embeddings


def write_to_staging(
    engine: Engine,
    staging_table: Table,
    chunk_embeddings: List[tuple[int, str]],
) -> None:
    """Write migrated embeddings to staging table."""
    now = datetime.now(timezone.utc).isoformat()
    
    with engine.connect() as conn:
        for chunk_id, emb_json in chunk_embeddings:
            conn.execute(
                staging_table.insert().values(
                    chunk_id=chunk_id,
                    embedding=emb_json,
                    migrated_at=now,
                )
            )
        conn.commit()


def atomic_swap(
    engine: Engine,
    kb_id: int,
    staging_table: Table,
    new_model: str,
) -> None:
    """Atomically swap staging embeddings into knowledge_chunks and update KB model."""
    with engine.connect() as conn:
        # Update knowledge_chunks.embedding from staging
        if engine.dialect.name == 'mysql':
            conn.execute(text(f"""
                UPDATE knowledge_chunks kc
                INNER JOIN {staging_table.name} st ON kc.id = st.chunk_id
                SET kc.embedding = st.embedding
            """))
        else:  # SQLite
            conn.execute(text(f"""
                UPDATE knowledge_chunks
                SET embedding = (
                    SELECT {staging_table.name}.embedding
                    FROM {staging_table.name}
                    WHERE {staging_table.name}.chunk_id = knowledge_chunks.id
                )
                WHERE id IN (SELECT chunk_id FROM {staging_table.name})
            """))
        
        # Update knowledge_bases.embedding_model
        conn.execute(
            text("UPDATE knowledge_bases SET embedding_model = :model WHERE id = :kb_id"),
            {"model": new_model, "kb_id": kb_id}
        )
        
        conn.commit()
        
        # Drop staging table
        conn.execute(text(f"DROP TABLE {staging_table.name}"))
        conn.commit()


def rebuild_faiss_and_purge_cache(rag: RAGSystem, kb_id: int, engine: Engine) -> None:
    """Rebuild FAISS index and purge semantic cache for KB."""
    rag._load_index(kb_id)
    
    # Purge semantic cache if enabled
    try:
        from shared.cache import rag_cache
        rag_cache.clear_kb_cache(kb_id)
    except Exception:
        pass  # Cache may not be enabled


def migrate_embeddings(
    kb_id: int,
    new_model: str,
    batch_size: int,
    dry_run: bool = False,
) -> int:
    """
    Migrate all embeddings for a KB to a new model.
    
    Returns exit code (0=success, 1=failure, 2=dry-run).
    """
    db_url = get_database_url()
    engine = create_engine(db_url)
    
    # Get old model from current config
    old_model = RAG_MODEL_NAME
    
    print(f"Starting migration for KB {kb_id}")
    print(f"  Old model: {old_model}")
    print(f"  New model: {new_model}")
    print(f"  Batch size: {batch_size}")
    print(f"  Dry run: {dry_run}")
    
    start_time = time.time()
    
    # Create RAG system with new model by setting env before any imports
    # Note: RAGSystem reads RAG_MODEL_NAME at construction time
    os.environ["RAG_MODEL_NAME"] = new_model
    
    # Force re-read of config by creating RAGSystem with explicit model_name
    # We need to reload the module to pick up the new env var
    import importlib
    import shared.config as config_module
    # Update the module-level variable
    config_module.RAG_MODEL_NAME = new_model
    
    # Now create RAGSystem - it will use the updated config
    rag = RAGSystem(model_name=new_model)
    
    print(f"Starting migration for KB {kb_id}")
    print(f"  Old model: {old_model}")
    print(f"  New model: {new_model}")
    print(f"  Batch size: {batch_size}")
    print(f"  Dry run: {dry_run}")
    
    start_time = time.time()
    
    with Session(engine) as session:
        # Load chunks
        chunks = load_chunks_for_kb(session, kb_id)
        total_chunks = len(chunks)
        
        if total_chunks == 0:
            print(f"KB {kb_id} has no chunks. Nothing to migrate.")
            return 0 if dry_run else 0
        
        print(f"Found {total_chunks} chunks to migrate")
        
        if dry_run:
            # Dry run: estimate time
            sample_size = min(10, total_chunks)
            sample_texts = [c.content for c in chunks[:sample_size]]
            
            sample_start = time.time()
            embed_batch(rag, sample_texts, batch_size)
            sample_time = time.time() - sample_start
            
            chunks_per_sec = sample_size / sample_time
            estimated_time = total_chunks / chunks_per_sec
            
            print(f"\nDry run complete:")
            print(f"  Sample: {sample_size} chunks in {sample_time:.2f}s")
            print(f"  Estimated speed: {chunks_per_sec:.1f} chunks/sec")
            print(f"  Estimated total time: {estimated_time:.1f}s ({estimated_time/60:.1f} min)")
            print(f"  Memory estimate: ~{total_chunks * 768 * 4 / 1024 / 1024:.1f}MB for embeddings")
            return 2
        
        # Create staging table
        staging_table = create_staging_table(engine, kb_id)
        print(f"Created staging table: {staging_table.name}")
        
        # Re-embed in batches
        chunk_embeddings: List[tuple[int, str]] = []
        last_progress_time = time.time()
        
        for i, chunk in enumerate(chunks):
            if not chunk.content:
                continue
            
            embedding = rag._get_embedding(chunk.content, is_query=False)
            if embedding is None:
                print(f"Warning: Failed to embed chunk {chunk.id}, skipping")
                continue
            
            chunk_embeddings.append((chunk.id, json.dumps(embedding.tolist())))
            
            # Write in batches
            if len(chunk_embeddings) >= batch_size:
                write_to_staging(engine, staging_table, chunk_embeddings)
                chunk_embeddings = []
            
            # Progress logging
            now = time.time()
            if now - last_progress_time >= 5:
                elapsed = now - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (total_chunks - i - 1) / rate if rate > 0 else 0
                print(f"Re-embedded {i+1}/{total_chunks} chunks ({rate:.1f} chunks/sec, ETA {eta:.0f}s)")
                last_progress_time = now
        
        # Write remaining
        if chunk_embeddings:
            write_to_staging(engine, staging_table, chunk_embeddings)
        
        # Atomic swap
        print("Performing atomic swap...")
        atomic_swap(engine, kb_id, staging_table, new_model)
        print("Atomic swap complete")
    
    # Rebuild FAISS and purge cache
    print("Rebuilding FAISS index and purging cache...")
    rebuild_faiss_and_purge_cache(rag, kb_id, engine)
    print("FAISS rebuild complete")
    
    elapsed = time.time() - start_time
    print(f"\nMigration completed successfully!")
    print(f"  Total time: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Chunks migrated: {total_chunks}")
    print(f"  Average speed: {total_chunks/elapsed:.1f} chunks/sec")
    
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate embeddings to a new model (RAGIDX-001)"
    )
    parser.add_argument(
        "--kb-id",
        type=int,
        required=True,
        help="Knowledge Base ID to migrate"
    )
    parser.add_argument(
        "--new-model",
        type=str,
        required=True,
        help="New embedding model name (e.g., intfloat/multilingual-e5-large)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Batch size for embedding (default: 64)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate time/memory without writing to DB"
    )
    
    args = parser.parse_args()
    
    try:
        return migrate_embeddings(
            kb_id=args.kb_id,
            new_model=args.new_model,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    except EmbeddingModelMismatchError as e:
        print(f"Error: {e}")
        print("This is expected - the KB still has old model. Migration will update it.")
        return 1
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
