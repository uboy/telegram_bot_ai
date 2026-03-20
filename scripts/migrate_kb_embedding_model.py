"""
Migration script to add embedding_model column to knowledge_bases table.

This script:
1. Adds the embedding_model column if it doesn't exist
2. Backfills existing KBs with the current RAG_MODEL_NAME
3. Is idempotent - safe to run multiple times

Usage:
    python scripts/migrate_kb_embedding_model.py [--rag-model-name intfloat/multilingual-e5-base]
"""
import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import Session

from shared.config import MYSQL_URL, DB_PATH, RAG_MODEL_NAME


def get_database_url() -> str:
    """Get database URL from config."""
    if MYSQL_URL:
        return MYSQL_URL
    return f"sqlite:///{DB_PATH}"


def migrate_schema(engine, rag_model_name: str) -> None:
    """Add embedding_model column and backfill existing KBs."""
    inspector = inspect(engine)
    has_column = False
    
    # Check if column exists (works for both MySQL and SQLite)
    try:
        columns = inspector.get_columns('knowledge_bases')
        has_column = any(col['name'] == 'embedding_model' for col in columns)
    except Exception as e:
        print(f"Warning: Could not inspect table: {e}")
    
    if has_column:
        print("Column 'embedding_model' already exists. Skipping schema migration.")
    else:
        print("Adding 'embedding_model' column to knowledge_bases table...")
        with engine.connect() as conn:
            # Add column (nullable to allow backfill)
            if engine.dialect.name == 'mysql':
                conn.execute(text(
                    "ALTER TABLE knowledge_bases ADD COLUMN embedding_model TEXT DEFAULT NULL"
                ))
            else:  # SQLite
                conn.execute(text(
                    "ALTER TABLE knowledge_bases ADD COLUMN embedding_model TEXT"
                ))
            conn.commit()
        print("Column added successfully.")
    
    # Backfill existing KBs with current model name
    print(f"Backfilling embedding_model with '{rag_model_name}' for existing KBs...")
    with Session(engine) as session:
        result = session.execute(text(
            "SELECT id, name, embedding_model FROM knowledge_bases WHERE embedding_model IS NULL"
        ))
        kb_rows = result.fetchall()
        
        if not kb_rows:
            print("No KBs require backfill (all already have embedding_model set).")
        else:
            for kb_id, kb_name, _ in kb_rows:
                session.execute(text(
                    "UPDATE knowledge_bases SET embedding_model = :model WHERE id = :id"
                ), {"model": rag_model_name, "id": kb_id})
                print(f"  - KB {kb_id} ({kb_name}): set to '{rag_model_name}'")
            session.commit()
            print(f"Backfill complete. Updated {len(kb_rows)} KB(s).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add embedding_model column to knowledge_bases table"
    )
    parser.add_argument(
        "--rag-model-name",
        default=RAG_MODEL_NAME,
        help=f"RAG model name for backfill (default: {RAG_MODEL_NAME})"
    )
    args = parser.parse_args()
    
    db_url = get_database_url()
    print(f"Connecting to database: {db_url}")
    
    engine = create_engine(db_url)
    
    try:
        migrate_schema(engine, args.rag_model_name)
        print("\nMigration completed successfully!")
        return 0
    except Exception as e:
        print(f"\nMigration failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
