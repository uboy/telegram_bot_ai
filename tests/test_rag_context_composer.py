import pytest

from backend.api.routes.rag import _select_evidence_pack_rows

pytest.importorskip("fastapi")

from backend.api.routes.rag import rag_query, rag_summary
from backend.schemas.rag import RAGQuery, RAGSummaryQuery


class DummyKB:
    settings = {
        "rag": {
            "single_page_mode": True,
            "single_page_top_k": 1,
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
        model_name = getattr(model, "__name__", "")
        if model_name == "KnowledgeBase":
            return DummyQuery(rows=[DummyKB()])
        return DummyQuery()


def _set_legacy_heuristics(monkeypatch, *, enabled: bool) -> None:
    import shared.config as shared_config

    monkeypatch.setattr(shared_config, "RAG_ORCHESTRATOR_V4", False, raising=False)
    monkeypatch.setattr(shared_config, "RAG_LEGACY_QUERY_HEURISTICS", enabled, raising=False)


def test_rag_query_evidence_pack_prefers_anchor_and_structural_neighbors(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_legacy_heuristics(monkeypatch, enabled=False)

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "Step 2 run `repo sync -c -j 8` from the repository root.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync and Build",
                    "section_path": "Sync Guide > Sync and Build",
                    "section_path_norm": "sync guide > sync and build",
                    "chunk_no": 2,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
                "rerank_score": 0.95,
                "distance": 0.05,
            },
            {
                "content": "Other unrelated overview with no concrete repo steps.",
                "metadata": {
                    "doc_title": "Overview",
                    "section_title": "Overview",
                    "section_path": "Overview",
                    "section_path_norm": "overview",
                    "chunk_no": 1,
                    "chunk_kind": "text",
                },
                "source_path": "doc://other",
                "source_type": "markdown",
                "rerank_score": 0.90,
                "distance": 0.10,
            },
        ]

    doc_chunks = {
        "doc://guide": [
            {
                "id": 11,
                "content": "Step 1 install prerequisites and initialize the repo tool.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync and Build",
                    "section_path": "Sync Guide > Sync and Build",
                    "section_path_norm": "sync guide > sync and build",
                    "chunk_no": 1,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
            },
            {
                "id": 12,
                "content": "Step 2 run `repo sync -c -j 8` from the repository root.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync and Build",
                    "section_path": "Sync Guide > Sync and Build",
                    "section_path_norm": "sync guide > sync and build",
                    "chunk_no": 2,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
            },
            {
                "id": 13,
                "content": "Step 3 verify the manifest and proceed to build after sync succeeds.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync and Build",
                    "section_path": "Sync Guide > Sync and Build",
                    "section_path_norm": "sync guide > sync and build",
                    "chunk_no": 3,
                    "chunk_kind": "text",
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
            },
        ],
        "doc://other": [
            {
                "id": 21,
                "content": "Other unrelated overview with no concrete repo steps.",
                "metadata": {
                    "doc_title": "Overview",
                    "section_title": "Overview",
                    "section_path": "Overview",
                    "section_path_norm": "overview",
                    "chunk_no": 1,
                    "chunk_kind": "text",
                },
                "source_path": "doc://other",
                "source_type": "markdown",
            }
        ],
    }

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())
    monkeypatch.setattr(
        rag_module,
        "_load_doc_chunks_for_context",
        lambda db, doc_id, kb_id=None: list(doc_chunks.get(doc_id, [])),  # noqa: ARG005
    )

    payload = RAGQuery(query="How do I sync the repo?", knowledge_base_id=1)
    result = rag_query(payload, db=DummyDB())

    assert "Step 1 install prerequisites" in result.answer
    assert "Step 2 run `repo sync -c -j 8`" in result.answer
    assert "Step 3 verify the manifest" in result.answer
    assert "Other unrelated overview" not in result.answer
    assert [source.source_path for source in result.sources] == ["doc://guide"]


def test_select_evidence_pack_rows_keeps_anchor_rows_ahead_of_support_rows():
    ranked_results = [
        {
            "id": 2,
            "content": "Anchor A2",
            "metadata": {
                "doc_title": "Doc A",
                "section_title": "Section A",
                "section_path": "Doc A > Section A",
                "section_path_norm": "doc a > section a",
                "chunk_no": 2,
                "chunk_kind": "text",
            },
            "source_path": "doc://a",
            "source_type": "markdown",
        },
        {
            "id": 11,
            "content": "Anchor B1",
            "metadata": {
                "doc_title": "Doc B",
                "section_title": "Section B",
                "section_path": "Doc B > Section B",
                "section_path_norm": "doc b > section b",
                "chunk_no": 1,
                "chunk_kind": "text",
            },
            "source_path": "doc://b",
            "source_type": "markdown",
        },
    ]
    doc_chunks = {
        "doc://a": [
            {
                "id": 1,
                "content": "Support A1",
                "metadata": {
                    "doc_title": "Doc A",
                    "section_title": "Section A",
                    "section_path": "Doc A > Section A",
                    "section_path_norm": "doc a > section a",
                    "chunk_no": 1,
                    "chunk_kind": "text",
                },
                "source_path": "doc://a",
                "source_type": "markdown",
            },
            ranked_results[0],
            {
                "id": 3,
                "content": "Support A3",
                "metadata": {
                    "doc_title": "Doc A",
                    "section_title": "Section A",
                    "section_path": "Doc A > Section A",
                    "section_path_norm": "doc a > section a",
                    "chunk_no": 3,
                    "chunk_kind": "text",
                },
                "source_path": "doc://a",
                "source_type": "markdown",
            },
        ],
        "doc://b": [
            ranked_results[1],
            {
                "id": 12,
                "content": "Support B2",
                "metadata": {
                    "doc_title": "Doc B",
                    "section_title": "Section B",
                    "section_path": "Doc B > Section B",
                    "section_path_norm": "doc b > section b",
                    "chunk_no": 2,
                    "chunk_kind": "text",
                },
                "source_path": "doc://b",
                "source_type": "markdown",
            },
        ],
    }

    result = _select_evidence_pack_rows(
        ranked_results=ranked_results,
        load_doc_chunks=lambda doc_id: list(doc_chunks.get(doc_id, [])),
        anchor_limit=2,
        context_limit=3,
    )

    assert [(row["content"], row["_context_reason"]) for row in result] == [
        ("Anchor A2", "primary"),
        ("Anchor B1", "primary"),
        ("Support A1", "adjacent_prev"),
    ]


def test_rag_query_context_excerpt_focuses_metric_sentence(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_legacy_heuristics(monkeypatch, enabled=False)

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": (
                    "б) совокупный прирост валового внутреннего продукта в 2030 году должен вырасти до 11,2 трлн рублей; "
                    "в) ежегодный объем оказанных услуг по разработке и реализации решений в области искусственного интеллекта "
                    "в 2030 году должен вырасти не менее чем до 60 млрд рублей по сравнению с 12 млрд рублей в 2022 году; "
                    "к) объем затрат организаций на внедрение технологий искусственного интеллекта должен вырасти до 850 млрд рублей."
                ),
                "metadata": {
                    "doc_title": "Target Metrics",
                    "section_title": "Metrics",
                    "section_path": "Target Metrics > Metrics",
                    "section_path_norm": "target metrics > metrics",
                    "chunk_no": 5,
                    "chunk_kind": "text",
                },
                "source_path": "doc://metrics",
                "source_type": "pdf",
                "rerank_score": 0.91,
                "distance": 0.09,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())
    monkeypatch.setattr(rag_module, "_load_doc_chunks_for_context", lambda db, doc_id, kb_id=None: [])  # noqa: ARG005

    payload = RAGQuery(
        query="Какой целевой ежегодный объем услуг по ИИ установлен на 2030 год?",
        knowledge_base_id=1,
    )
    result = rag_query(payload, db=DummyDB())

    assert "60 млрд рублей" in result.answer
    assert "850 млрд рублей" not in result.answer


def test_rag_summary_uses_query_focused_excerpt(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": (
                    "б) совокупный прирост валового внутреннего продукта в 2030 году должен вырасти до 11,2 трлн рублей; "
                    "в) ежегодный объем оказанных услуг по разработке и реализации решений в области искусственного интеллекта "
                    "в 2030 году должен вырасти не менее чем до 60 млрд рублей по сравнению с 12 млрд рублей в 2022 году; "
                    "к) объем затрат организаций на внедрение технологий искусственного интеллекта должен вырасти до 850 млрд рублей."
                ),
                "metadata": {
                    "doc_title": "Target Metrics",
                    "section_title": "Metrics",
                    "section_path": "Target Metrics > Metrics",
                    "section_path_norm": "target metrics > metrics",
                    "chunk_no": 5,
                    "chunk_kind": "text",
                    "source_updated_at": "2025-01-01T10:00:00",
                },
                "source_path": "doc://metrics",
                "source_type": "pdf",
                "rerank_score": 0.91,
                "distance": 0.09,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "ai_manager", type("Y", (), {"query": staticmethod(lambda prompt: prompt)})())
    monkeypatch.setattr(rag_module, "_load_doc_chunks_for_context", lambda db, doc_id, kb_id=None: [])  # noqa: ARG005

    payload = RAGSummaryQuery(
        query="Какой целевой ежегодный объем услуг по ИИ установлен на 2030 год?",
        knowledge_base_id=1,
        mode="summary",
    )
    result = rag_summary(payload, db=DummyDB())

    assert "60 млрд рублей" in result.answer
    assert "850 млрд рублей" not in result.answer


def test_rag_query_returns_extractive_fallback_on_provider_timeout(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_legacy_heuristics(monkeypatch, enabled=False)

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "Сначала открой раздел проекта и синхронизируй репозиторий через repo sync -c -j 8.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync",
                    "section_path": "Sync Guide > Sync",
                    "section_path_norm": "sync guide > sync",
                    "chunk_no": 1,
                    "chunk_kind": "text",
                    "page_no": 6,
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
                "rerank_score": 0.95,
                "distance": 0.05,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(
        rag_module,
        "ai_manager",
        type(
            "Y",
            (),
            {
                "query": staticmethod(
                    lambda _prompt: "Ошибка подключения к Ollama: HTTPConnectionPool(host='x', port=11434): Read timed out. (read timeout=120)"
                )
            },
        )(),
    )
    monkeypatch.setattr(rag_module, "_load_doc_chunks_for_context", lambda db, doc_id, kb_id=None: [])  # noqa: ARG005

    payload = RAGQuery(query="Как синхронизировать репозиторий?", knowledge_base_id=1)
    result = rag_query(payload, db=DummyDB())

    assert "Показываю наиболее релевантные фрагменты" in result.answer
    assert "repo sync -c -j 8" in result.answer
    assert "Read timed out" not in result.answer
    assert [source.source_path for source in result.sources] == ["doc://guide"]


def test_rag_query_fallback_still_runs_answer_safety_filters(monkeypatch):
    from backend.api.routes import rag as rag_module

    _set_legacy_heuristics(monkeypatch, enabled=False)

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "Используй repo sync -c -j 8 и не переходи по http://malicious.example/phish.",
                "metadata": {
                    "doc_title": "Sync Guide",
                    "section_title": "Sync",
                    "section_path": "Sync Guide > Sync",
                    "section_path_norm": "sync guide > sync",
                    "chunk_no": 1,
                    "chunk_kind": "text",
                    "page_no": 6,
                },
                "source_path": "doc://guide",
                "source_type": "markdown",
                "rerank_score": 0.95,
                "distance": 0.05,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(rag_module, "strip_unknown_citations", lambda answer, context: answer)
    monkeypatch.setattr(
        rag_module,
        "strip_untrusted_urls",
        lambda answer, context, allowed_urls=None: answer.replace(  # noqa: ARG005
            "http://malicious.example/phish",
            "[url-removed]",
        ),
    )
    monkeypatch.setattr(
        rag_module,
        "sanitize_commands_in_answer",
        lambda answer, context: answer + "\n[safety-processed]",  # noqa: ARG005
    )
    monkeypatch.setattr(
        rag_module,
        "ai_manager",
        type("Y", (), {"query": staticmethod(lambda _prompt: "Ошибка при обращении к Ollama: 503")})(),
    )
    monkeypatch.setattr(rag_module, "_load_doc_chunks_for_context", lambda db, doc_id, kb_id=None: [])  # noqa: ARG005

    payload = RAGQuery(query="Как синхронизировать репозиторий?", knowledge_base_id=1)
    result = rag_query(payload, db=DummyDB())

    assert "repo sync -c -j 8" in result.answer
    assert "[url-removed]" in result.answer
    assert "[safety-processed]" in result.answer
    assert "503" not in result.answer


def test_rag_summary_returns_extractive_fallback_on_provider_timeout(monkeypatch):
    from backend.api.routes import rag as rag_module

    def fake_search(query, knowledge_base_id=None, top_k=8):  # noqa: ARG001
        return [
            {
                "content": "Политика требует провести проверку безопасности и сверку контрольных списков перед релизом.",
                "metadata": {
                    "doc_title": "Release Policy",
                    "section_title": "Release Checks",
                    "section_path": "Release Policy > Release Checks",
                    "section_path_norm": "release policy > release checks",
                    "chunk_no": 2,
                    "chunk_kind": "text",
                    "page_no": 4,
                    "source_updated_at": "2025-01-01T10:00:00",
                },
                "source_path": "doc://policy",
                "source_type": "pdf",
                "rerank_score": 0.91,
                "distance": 0.09,
            }
        ]

    monkeypatch.setattr(rag_module, "rag_system", type("X", (), {"search": staticmethod(fake_search)})())
    monkeypatch.setattr(
        rag_module,
        "ai_manager",
        type("Y", (), {"query": staticmethod(lambda _prompt: "Ошибка при обращении к Ollama: 503")})(),
    )
    monkeypatch.setattr(rag_module, "_load_doc_chunks_for_context", lambda db, doc_id, kb_id=None: [])  # noqa: ARG005

    payload = RAGSummaryQuery(query="Какие проверки нужны перед релизом?", knowledge_base_id=1, mode="summary")
    result = rag_summary(payload, db=DummyDB())

    assert "Показываю наиболее релевантные фрагменты" in result.answer
    assert "проверку безопасности" in result.answer
    assert "503" not in result.answer
    assert [source.source_path for source in result.sources] == ["doc://policy"]
