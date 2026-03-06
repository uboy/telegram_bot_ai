from scripts import rag_eval_baseline_runner as runner


def test_render_markdown_report_contains_metrics_table():
    status = {
        "run_id": "eval_1",
        "suite_name": "rag-general-v1",
        "baseline_run_id": None,
        "status": "completed",
        "started_at": "2026-03-06T10:00:00+00:00",
        "finished_at": "2026-03-06T10:01:00+00:00",
        "metrics": {
            "total_cases": 28,
            "slices": ["overall", "ru"],
            "thresholds": {"recall_at_10": 0.6},
        },
        "results": [
            {
                "slice_name": "ru",
                "metric_name": "recall_at_10",
                "metric_value": 0.75,
                "threshold_value": 0.6,
                "passed": True,
            }
        ],
    }

    md = runner.render_markdown_report(status)

    assert "# RAG Eval Baseline Report" in md
    assert "| Slice | Metric | Value | Threshold | Passed |" in md
    assert "| ru | recall_at_10 | 0.7500 | 0.6000 | yes |" in md


def test_parse_slices_deduplicates_and_normalizes():
    assert runner._parse_slices(" RU, en,ru , howto ") == ["ru", "en", "howto"]
    assert runner._parse_slices(None) is None
