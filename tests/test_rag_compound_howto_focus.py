import pytest

pytest.importorskip("fastapi")

from backend.api.routes.rag import _focus_compound_howto_rows, rag_query
from backend.schemas.rag import RAGQuery


class DummyKB:
    settings = {
        "rag": {
            "single_page_mode": True,
            "single_page_top_k": 2,
            "full_page_context_multiplier": 5,
        }
    }


class DummyQuery:
    def __init__(self, rows=None):
        self._rows = rows or []

    def filter_by(self, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else DummyKB()

    def all(self):
        return list(self._rows)


class DummyDB:
    def query(self, model):
        if getattr(model, "__name__", "") == "KnowledgeBase":
            return DummyQuery(rows=[DummyKB()])
        return DummyQuery()


def _set_generalized_mode(monkeypatch) -> None:
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", False, raising=False)


def test_focus_compound_howto_rows_prefers_syncbuild_family():
    rows = [
        {
            "content": "Apply Linux Previewer patch for mirror v136.",
            "metadata": {
                "doc_title": "Stable Build v136",
                "section_title": "Linux Previewer Patch",
                "section_path": "Stable Build v136 > Linux Previewer Patch",
                "section_path_norm": "stable build v136 > linux previewer patch",
                "chunk_no": 1,
                "chunk_kind": "text",
            },
            "source_path": "doc://stable-v136",
            "source_type": "markdown",
            "rank_score": 0.9,
        },
        {
            "content": "repo init -u https://gitcode.com/openharmony/manifest.git -b master --no-repo-verify",
            "metadata": {
                "doc_title": "Sync&Build",
                "section_title": "Initialize repository and sync code",
                "section_path": "Sync&Build > Initialize repository and sync code",
                "section_path_norm": "sync&build > initialize repository and sync code",
                "chunk_no": 2,
                "chunk_kind": "text",
            },
            "source_path": "doc://sync-build",
            "source_type": "markdown",
            "rank_score": 0.86,
        },
    ]

    focused = _focus_compound_howto_rows("how to build and sync", rows)

    assert [row["source_path"] for row in focused] == ["doc://sync-build"]


def test_focus_compound_howto_rows_prefers_distinctive_previewer_family():
    rows = [
        {
            "content": "./build.sh --product-name previewer",
            "metadata": {
                "doc_title": "Build Guide",
                "section_title": "Build steps",
                "section_path": "Build Guide > Build steps",
                "section_path_norm": "build guide > build steps",
                "chunk_no": 1,
                "chunk_kind": "text",
            },
            "source_path": "doc://generic-build",
            "source_type": "markdown",
            "rank_score": 0.91,
        },
        {
            "content": "wget -c https://example.test/spreviewer_arkts12_master.patch\npatch -p1 < spreviewer_arkts12_master.patch",
            "metadata": {
                "doc_title": "Linux Previewer Guide",
                "section_title": "MASTER branch patch",
                "section_path": "Linux Previewer Guide > MASTER branch patch",
                "section_path_norm": "linux previewer guide > master branch patch",
                "chunk_no": 2,
                "chunk_kind": "text",
            },
            "source_path": "doc://previewer-master",
            "source_type": "markdown",
            "rank_score": 0.86,
        },
    ]

    focused = _focus_compound_howto_rows("how to build previewer for master branch", rows)

    assert [row["source_path"] for row in focused] == ["doc://previewer-master"]


def test_rag_query_compound_howto_focuses_one_procedural_family(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_generalized_mode(monkeypatch)

    search_results = [
        {
            "content": "Apply Linux Previewer patch for mirror v136.",
            "metadata": {
                "doc_title": "Stable Build v136",
                "section_title": "Linux Previewer Patch",
                "section_path": "Stable Build v136 > Linux Previewer Patch",
                "section_path_norm": "stable build v136 > linux previewer patch",
                "chunk_no": 1,
                "chunk_kind": "text",
            },
            "source_path": "doc://stable-v136",
            "source_type": "markdown",
            "multi_query_score": 0.9,
            "distance": 0.1,
        },
        {
            "content": "Patch old v135 previewer before regeneration.",
            "metadata": {
                "doc_title": "Stable Build & Regeneration v135",
                "section_title": "Possible build issues",
                "section_path": "Stable Build & Regeneration v135 > Possible build issues",
                "section_path_norm": "stable build regeneration v135 > possible build issues",
                "chunk_no": 1,
                "chunk_kind": "text",
            },
            "source_path": "doc://stable-v135",
            "source_type": "markdown",
            "multi_query_score": 0.88,
            "distance": 0.12,
        },
        {
            "content": "repo init -u https://gitcode.com/openharmony/manifest.git -b master --no-repo-verify",
            "metadata": {
                "doc_title": "Sync&Build",
                "section_title": "Initialize repository and sync code",
                "section_path": "Sync&Build > Initialize repository and sync code",
                "section_path_norm": "sync&build > initialize repository and sync code",
                "chunk_no": 2,
                "chunk_kind": "text",
            },
            "source_path": "doc://sync-build",
            "source_type": "markdown",
            "multi_query_score": 0.86,
            "distance": 0.14,
        },
    ]
    doc_chunks = {
        "doc://sync-build": [
            dict(search_results[2]),
            {
                "content": "repo sync -c -j 8\nbuild/prebuilts_download.sh",
                "metadata": {
                    "doc_title": "Sync&Build",
                    "section_title": "Initialize repository and sync code",
                    "section_path": "Sync&Build > Initialize repository and sync code",
                    "section_path_norm": "sync&build > initialize repository and sync code",
                    "chunk_no": 3,
                    "chunk_kind": "text",
                },
                "source_path": "doc://sync-build",
                "source_type": "markdown",
            },
        ]
    }

    monkeypatch.setattr(
        rag_module,
        "rag_system",
        type("FakeRag", (), {"search": staticmethod(lambda query, knowledge_base_id=None, top_k=8: list(search_results))})(),
    )
    monkeypatch.setattr(
        rag_module,
        "_load_doc_chunks_for_context",
        lambda db, doc_id, kb_id=None: list(doc_chunks.get(doc_id, [])),  # noqa: ARG005
    )
    monkeypatch.setattr(
        rag_module,
        "_focus_compound_howto_rows",
        lambda query, rows: [row for row in rows if row.get("source_path") == "doc://sync-build"],
    )
    monkeypatch.setattr(rag_module, "ai_manager", type("EchoAi", (), {"query": staticmethod(lambda prompt: prompt)})())

    result = rag_query(RAGQuery(query="how to build and sync", knowledge_base_id=1), db=DummyDB())

    assert "repo init" in result.answer
    assert "repo sync -c -j 8" in result.answer
    assert "build/prebuilts_download.sh" in result.answer
    assert "Previewer patch" not in result.answer
    assert "Possible build issues" not in result.answer
    assert [source.source_path for source in result.sources] == ["doc://sync-build"]


def test_rag_query_legacy_howto_does_not_hardcode_syncbuild_title(monkeypatch):
    from backend.api.routes import rag as rag_module
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", True, raising=False)

    search_results = [
        {
            "content": "Previewer patch notes for a legacy mirror build.",
            "metadata": {
                "doc_title": "Sync&Build",
                "section_title": "Overview",
                "section_path": "Sync&Build > Overview",
                "section_path_norm": "sync&build > overview",
                "chunk_no": 1,
                "chunk_kind": "text",
            },
            "source_path": "doc://syncbuild-overview",
            "source_type": "markdown",
            "distance": 0.10,
            "rerank_score": 0.90,
        },
        {
            "content": "repo init -u https://gitcode.com/openharmony/manifest.git -b master --no-repo-verify\nrepo sync -c -j 8\nbuild/prebuilts_download.sh",
            "metadata": {
                "doc_title": "Platform Build",
                "section_title": "Initialize repository and sync code",
                "section_path": "Platform Build > Initialize repository and sync code",
                "section_path_norm": "platform build > initialize repository and sync code",
                "chunk_no": 2,
                "chunk_kind": "text",
            },
            "source_path": "doc://platform-build",
            "source_type": "markdown",
            "distance": 0.20,
            "rerank_score": 0.80,
        },
    ]

    monkeypatch.setattr(
        rag_module,
        "rag_system",
        type("FakeRag", (), {"search": staticmethod(lambda query, knowledge_base_id=None, top_k=8: list(search_results))})(),
    )
    monkeypatch.setattr(rag_module, "ai_manager", type("EchoAi", (), {"query": staticmethod(lambda prompt: prompt)})())

    result = rag_query(RAGQuery(query="how to build and sync", knowledge_base_id=1), db=DummyDB())

    assert "repo init" in result.answer
    assert "Previewer patch notes" not in result.answer
    assert [source.source_path for source in result.sources] == ["doc://platform-build"]
