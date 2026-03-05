from backend.services import rag_eval_service as eval_module


def test_eval_service_sync_run_completes(monkeypatch):
    service = eval_module.RAGEvalService()
    cases = [
        {"id": "c1", "query": "how to build", "expected_source": "sync.md"},
        {"id": "c2", "query": "how to install", "expected_source": "setup.md"},
    ]

    monkeypatch.setattr(eval_module, "_load_yaml_suite", lambda: cases)

    def fake_search(query, knowledge_base_id=None, top_k=10):  # noqa: ARG001
        if "build" in query:
            return [{"source_path": "docs/sync.md", "content": "repo sync", "origin": "qdrant"}]
        return [{"source_path": "docs/setup.md", "content": "install deps", "origin": "qdrant"}]

    monkeypatch.setattr(
        eval_module,
        "rag_system",
        type("DummyRag", (), {"search": staticmethod(fake_search)})(),
    )

    run_id = service.start_run(
        suite_name="rag-general-v1",
        baseline_run_id=None,
        slices=["overall", "howto"],
        run_async=False,
    )
    status = service.get_run_status(run_id)

    assert status is not None
    assert status["status"] == "completed"
    assert status["metrics"]["total_cases"] == 2
    assert len(status["results"]) == 6  # 2 slices * 3 metrics
    overall_recall = [
        row for row in status["results"]
        if row["slice_name"] == "overall" and row["metric_name"] == "recall_at_10"
    ]
    assert len(overall_recall) == 1
    assert overall_recall[0]["metric_value"] == 1.0


def test_eval_service_sync_run_fails_without_cases(monkeypatch):
    service = eval_module.RAGEvalService()
    monkeypatch.setattr(eval_module, "_load_yaml_suite", lambda: [])

    run_id = service.start_run(
        suite_name="rag-general-v1",
        baseline_run_id=None,
        slices=["overall"],
        run_async=False,
    )
    status = service.get_run_status(run_id)

    assert status is not None
    assert status["status"] == "failed"
    assert "No eval cases found" in (status.get("error_message") or "")
