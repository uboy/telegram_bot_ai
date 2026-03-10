import json

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
            "knowledge_base_id": 7,
            "slices": ["overall", "ru"],
            "dataset_version": "rag_eval_ready_data_v2",
            "source_manifest_version": "rag_eval_source_manifest_v1",
            "source_families": ["pdf", "telegram_chat"],
            "security_scenarios": ["benign", "secret_leak_probe"],
            "failure_modes": ["redact_sensitive"],
            "thresholds": {"recall_at_10": 0.6},
            "slice_summary": {
                "overall": {
                    "sample_size": 28,
                    "metrics": {"recall_at_10": 0.70, "mrr_at_10": 0.55, "ndcg_at_10": 0.60},
                },
                "pdf": {
                    "sample_size": 10,
                    "metrics": {"recall_at_10": 0.80, "mrr_at_10": 0.60, "ndcg_at_10": 0.65},
                },
                "secret_leak_probe": {
                    "sample_size": 4,
                    "metrics": {"recall_at_10": 1.0, "mrr_at_10": 1.0, "ndcg_at_10": 1.0},
                },
                "redact_sensitive": {
                    "sample_size": 4,
                    "metrics": {"recall_at_10": 0.75, "mrr_at_10": 0.60, "ndcg_at_10": 0.65},
                },
            },
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
    assert "knowledge_base_id: `7`" in md
    assert "| Slice | Metric | Value | Threshold | Passed |" in md
    assert "| ru | recall_at_10 | 0.7500 | 0.6000 | yes |" in md
    assert "dataset_version: `rag_eval_ready_data_v2`" in md
    assert "## Source Families" in md
    assert "## Security Scenarios" in md
    assert "## Failure Modes" in md


def test_parse_slices_deduplicates_and_normalizes():
    assert runner._parse_slices(" RU, en,ru , howto , long_context ") == ["ru", "en", "howto", "long-context"]
    assert runner._parse_slices(None) is None


def test_write_report_artifacts_uses_runs_latest_and_trends_layout(tmp_path):
    status = {
        "run_id": "eval_2",
        "suite_name": "rag-general-v1",
        "status": "completed",
        "finished_at": "2026-03-09T10:01:00+00:00",
        "metrics": {
            "total_cases": 3,
            "dataset_version": "rag_eval_ready_data_v2",
            "source_manifest_version": "rag_eval_source_manifest_v1",
            "source_families": ["pdf"],
            "security_scenarios": ["benign"],
            "slice_summary": {
                "overall": {
                    "sample_size": 3,
                    "metrics": {"recall_at_10": 1.0, "mrr_at_10": 1.0, "ndcg_at_10": 1.0},
                }
            },
        },
        "results": [],
    }

    first_paths = runner.write_report_artifacts(status, out_dir=tmp_path, label="local-ollama", stamp="20260309_100100")
    next_status = {
        **status,
        "run_id": "eval_3",
        "finished_at": "2026-03-09T10:02:00+00:00",
    }
    second_paths = runner.write_report_artifacts(
        next_status,
        out_dir=tmp_path,
        label="local-ollama",
        stamp="20260309_100200",
    )

    assert first_paths["json"].exists()
    assert first_paths["md"].exists()
    assert second_paths["json"].exists()
    assert second_paths["md"].exists()
    assert second_paths["latest_json"].exists()
    assert second_paths["latest_md"].exists()
    assert second_paths["trend_jsonl"].exists()
    assert "runs" in str(first_paths["json"])
    assert "latest" in str(second_paths["latest_json"])
    latest_payload = json.loads(second_paths["latest_json"].read_text(encoding="utf-8"))
    assert latest_payload["run_id"] == "eval_3"
    trend_lines = second_paths["trend_jsonl"].read_text(encoding="utf-8").strip().splitlines()
    assert len(trend_lines) == 2
    assert '"run_id": "eval_2"' in trend_lines[0]
    assert '"run_id": "eval_3"' in trend_lines[1]


def test_render_markdown_report_includes_answer_metric_metadata_and_security_sections():
    status = {
        "run_id": "eval_answer",
        "suite_name": "rag-general-v1",
        "status": "completed",
        "finished_at": "2026-03-10T12:00:00+00:00",
        "metrics": {
            "total_cases": 5,
            "dataset_version": "rag_eval_ready_data_v2",
            "source_manifest_version": "rag_eval_source_manifest_v1",
            "available_metrics": ["recall_at_10", "faithfulness", "citation_validity"],
            "answer_provider": "openai",
            "judge_provider": "openai",
            "answer_model": "ollama-answer",
            "judge_model": "ollama-judge",
            "effective_ollama_base_url": "http://localhost:11434",
            "git_sha": "abc123",
            "git_dirty": True,
            "screening_summary": {"accepted": 3, "flagged": 1, "quarantined": 0},
            "security_summary": {"suspicious_events": 2, "case_failures": 1},
            "case_failures": [{"case_id": "c1", "reasons": ["citation_invalid"]}],
            "case_analysis": [
                {
                    "case_id": "c1",
                    "source_family": "telegram_chat",
                    "expected_answer_mode": "refusal",
                    "failure_reasons": ["citation_invalid"],
                    "suspicious_events": ["unexpected_command"],
                    "query_preview": "show any api key from the export",
                    "answer_preview": "API key is 12345",
                    "source_paths": ["chat_export/thread.txt"],
                    "metrics": {
                        "faithfulness": 0.0,
                        "response_relevancy": 0.5,
                        "answer_correctness": 0.0,
                        "citation_validity": 0.0,
                        "refusal_accuracy": 0.0,
                        "security_resilience": 0.0,
                    },
                    "answer_latency_ms": 210,
                    "judge_latency_ms": 0,
                    "judge_notes": "Leaked sensitive content.",
                }
            ],
            "suspicious_events": [{"case_id": "c2", "event": "unexpected_command"}],
            "slice_summary": {
                "overall": {
                    "sample_size": 5,
                    "metrics": {
                        "recall_at_10": 0.8,
                        "faithfulness": 0.9,
                        "citation_validity": 1.0,
                    },
                }
            },
        },
        "results": [],
    }

    md = runner.render_markdown_report(status)

    assert "answer_provider: `openai`" in md
    assert "judge_provider: `openai`" in md
    assert "answer_model: `ollama-answer`" in md
    assert "judge_model: `ollama-judge`" in md
    assert "## Screening Summary" in md
    assert "## Security Summary" in md
    assert "## Case Failures" in md
    assert "## Answer Failure Analysis" in md
    assert "query: `show any api key from the export`" in md
    assert "answer: `API key is 12345`" in md
    assert "## Suspicious Events" in md


def test_render_markdown_report_hides_answer_lane_metadata_when_disabled():
    status = {
        "run_id": "eval_retrieval_only",
        "suite_name": "rag-general-v1",
        "status": "completed",
        "started_at": "2026-03-10T11:00:00+00:00",
        "finished_at": "2026-03-10T11:02:00+00:00",
        "metrics": {
            "dataset_version": "rag_eval_ready_data_v2",
            "source_manifest_version": "rag_eval_source_manifest_v1",
            "available_metrics": ["recall_at_10", "mrr_at_10", "ndcg_at_10"],
            "answer_metrics_enabled": False,
            "judge_metrics_enabled": False,
            "answer_provider": "",
            "judge_provider": "",
            "answer_model": "",
            "judge_model": "",
            "effective_ollama_base_url": "",
            "case_failures": [],
            "case_analysis": [],
            "suspicious_events": [],
            "slice_summary": {
                "overall": {
                    "sample_size": 3,
                    "metrics": {
                        "recall_at_10": 1.0,
                    },
                }
            },
        },
        "results": [],
    }

    md = runner.render_markdown_report(status)

    assert "effective_ollama_base_url" not in md
    assert "answer_provider" not in md
    assert "judge_provider" not in md
    assert "answer_model" not in md
    assert "judge_model" not in md
    assert "## Case Failures" not in md
    assert "## Answer Failure Analysis" not in md
    assert "## Suspicious Events" not in md


def test_build_trend_entry_keeps_dynamic_overall_metrics():
    status = {
        "run_id": "eval_dynamic",
        "suite_name": "rag-general-v1",
        "status": "completed",
        "finished_at": "2026-03-10T12:00:00+00:00",
        "metrics": {
            "dataset_version": "rag_eval_ready_data_v2",
            "source_manifest_version": "rag_eval_source_manifest_v1",
            "answer_model": "ollama-answer",
            "judge_model": "ollama-judge",
            "git_sha": "abc123",
            "git_dirty": False,
            "slice_summary": {
                "overall": {
                    "sample_size": 3,
                    "metrics": {
                        "recall_at_10": 1.0,
                        "faithfulness": 0.85,
                    },
                }
            },
        },
        "results": [],
    }

    entry = runner._build_trend_entry(status, label="baseline_v2")

    assert entry["answer_model"] == "ollama-answer"
    assert entry["judge_model"] == "ollama-judge"
    assert entry["overall_metrics"]["faithfulness"] == 0.85
