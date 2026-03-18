import json
import os
from types import SimpleNamespace
import numpy as np

os.environ["MYSQL_URL"] = ""
os.environ.setdefault("DB_PATH", "data/test-rag-metadata-field.db")

import shared.rag_system as rag_module
from shared.rag_system import rag_system


def _chunk(*, content: str, source_path: str, doc_title: str = "", section_title: str = "", section_path: str = ""):
    metadata = {
        "doc_title": doc_title,
        "section_title": section_title,
        "section_path": section_path,
    }
    return SimpleNamespace(
        content=content,
        source_type="wiki",
        source_path=source_path,
        chunk_metadata=json.dumps(metadata, ensure_ascii=False),
    )


def test_metadata_field_search_prefers_sync_build_doc_for_broad_build_sync_query():
    chunks = [
        _chunk(
            content="KOALA_BZ=1 npm run ohos-sdk",
            source_path="https://gitee.com/org/repo/wikis/Arkoala/Arkoala%20build%20and%20run",
            doc_title="Arkoala build and run",
            section_title="Copy arkoala-plugin.js to your SDK",
            section_path="Arkoala > Build and run",
        ),
        _chunk(
            content="Run repo sync and build the image from the synchronized tree.",
            source_path="https://gitee.com/org/repo/wikis/Sync%26Build/Sync%26Build",
            doc_title="Sync&Build",
            section_title="Initialize repository and sync code",
            section_path="Sync&Build > Initialize repository and sync code",
        ),
    ]

    results = rag_system._metadata_field_search("how to build and sync", chunks, top_k=2)

    assert len(results) == 2
    assert results[0]["source_path"].endswith("/Sync%26Build/Sync%26Build")
    assert results[0]["origin"] == "field"


def test_metadata_field_search_prefers_sync_code_section_over_incidental_mirror_mentions():
    chunks = [
        _chunk(
            content="hdc file send ... koala_mirror artifacts",
            source_path="https://gitee.com/org/repo/wikis/Features/C-API/Run%20HelloWorld%20v133",
            doc_title="Run HelloWorld v133",
            section_title="Partial update rk3568 with koala_mirror artifacts",
            section_path="Features > C-API > Run HelloWorld v133",
        ),
        _chunk(
            content="Initialize the repository and sync code with the local mirror before build.",
            source_path="https://gitee.com/org/repo/wikis/Sync%26Build/Sync%26Build",
            doc_title="Sync&Build",
            section_title="Initialize repository and sync code",
            section_path="Sync&Build > Initialize repository and sync code",
        ),
    ]

    results = rag_system._metadata_field_search("how to sync code with local mirror", chunks, top_k=2)

    assert len(results) == 2
    assert results[0]["source_path"].endswith("/Sync%26Build/Sync%26Build")
    assert results[0]["origin"] == "field"


def test_search_preserves_fused_field_order_without_reranker(monkeypatch):
    monkeypatch.setattr(rag_module, "HAS_EMBEDDINGS", True)

    class FakeIndex:
        def search(self, query_embedding_norm, top_k):
            return np.array([[0.95, 0.80]], dtype="float32"), np.array([[0, 1]], dtype="int64")

    system = rag_module.RAGSystem.__new__(rag_module.RAGSystem)
    dense_chunk = _chunk(
        content="Build Panda Runtime",
        source_path="https://example.test/Features/Previewer/Build-Panda",
        doc_title="ArkTS 1.2",
        section_title="Build Panda Runtime",
        section_path="Features > Previewer > ArkTS 1.2",
    )
    field_chunk = _chunk(
        content="repo init && repo sync && ./build/prebuilts_download.sh",
        source_path="https://example.test/Sync%26Build/Sync%26Build",
        doc_title="Sync&Build",
        section_title="Initialize repository and sync code",
        section_path="Sync&Build > Initialize repository and sync code",
    )
    system.encoder = object()
    system.enable_rerank = False
    system.reranker = None
    system.bm25_index_by_kb = {1: {"dummy": 1}}
    system.bm25_chunks_by_kb = {1: [dense_chunk, field_chunk]}
    system.bm25_index_all = None
    system.bm25_chunks_all = []
    system.index_by_kb = {1: FakeIndex()}
    system.chunks_by_kb = {1: [dense_chunk, field_chunk]}
    system.index = None
    system.chunks = []
    system.dimension = 768
    system.index_dimension_by_kb = {1: 768}
    system.dense_candidate_budget = 2
    system.bm25_candidate_budget = 2
    system.rerank_top_n = 0
    system._qdrant_enabled = lambda: False
    system._get_embedding = lambda query, is_query=False: np.ones(768, dtype="float32")  # noqa: ARG005
    system._load_index = lambda knowledge_base_id: None
    system._simple_search = lambda query, knowledge_base_id, top_k: []
    system._build_bm25_index = lambda chunks: {"dummy": 1}
    system._bm25_search = lambda query, index, top_k: [1, 0]
    system._metadata_field_search = lambda query, chunks, top_k: [
        {
            "content": field_chunk.content,
            "metadata": json.loads(field_chunk.chunk_metadata),
            "source_type": field_chunk.source_type,
            "source_path": field_chunk.source_path,
            "distance": 0.1,
            "origin": "field",
        }
    ]

    results = system.search("how to build and sync", knowledge_base_id=1, top_k=2)

    assert len(results) == 2
    assert results[0]["origin"] == "field"
    assert results[0]["source_path"].endswith("/Sync%26Build/Sync%26Build")
