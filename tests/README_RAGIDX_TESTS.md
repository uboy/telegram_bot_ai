# RAG Index Lifecycle Tests

Tests for RAGIDX-001 implementation.

## Running Tests

### Unit Tests (SQLite - no migration required)

```bash
.venv\Scripts\python -m pytest tests/test_rag_index_lifecycle.py::TestEmbeddingModelColumn -v
```

### Integration Tests (MySQL - migration required)

Before running full test suite, run the schema migration:

```bash
# Run migration to add embedding_model column
.venv\Scripts\python scripts\migrate_kb_embedding_model.py --rag-model-name intfloat/multilingual-e5-base

# Then run tests
.venv\Scripts\python -m pytest tests/test_rag_index_lifecycle.py -v
```

## Test Coverage

| Test Class | Tests | Status |
|---|---|---|
| `TestMismatchDetection` | 3 | Requires MySQL migration |
| `TestReindexDocument` | 2 | Requires MySQL migration |
| `TestDebounceRebuild` | 2 | Requires MySQL migration |
| `TestMigrationCLI` | 3 | Requires MySQL migration |
| `TestEmbeddingModelColumn` | 2 | PASS (uses SQLite) |

## Notes

- Tests use `rag_system` global instance which is configured for MySQL via `.env`
- SQLite tests in `TestEmbeddingModelColumn` verify basic ORM functionality
- Full integration tests require MySQL with `embedding_model` column migrated
- Debounce tests have 5+ second sleep per test (configurable via `RAG_REBUILD_DEBOUNCE_SEC`)
