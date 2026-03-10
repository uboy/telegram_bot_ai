import pytest

from scripts import rag_eval_quality_gate as gate


def _row(value: float, sample_size: int, values):
    return {
        "metric_value": value,
        "details": {
            "sample_size": sample_size,
            "values": list(values),
        },
    }


def test_bootstrap_ci_delta_deterministic_for_constant_values():
    ci = gate.bootstrap_ci_delta([1.0, 1.0, 1.0], [0.5, 0.5, 0.5], samples=300, seed=7)
    assert ci is not None
    assert ci[0] == 0.5
    assert ci[1] == 0.5


def test_evaluate_gate_passes_with_baseline_and_bootstrap():
    run_rows = {
        ("ru", "recall_at_10"): _row(0.85, 1000, [1.0] * 850 + [0.0] * 150),
    }
    baseline_rows = {
        ("ru", "recall_at_10"): _row(0.80, 1000, [1.0] * 800 + [0.0] * 200),
    }

    report = gate.evaluate_gate(
        run_id="run-1",
        run_rows=run_rows,
        baseline_id="base-1",
        baseline_rows=baseline_rows,
        required_slices=["ru"],
        metrics=["recall_at_10"],
        thresholds={"recall_at_10": 0.6},
        min_sample_size=100,
        negative_margin=-0.01,
        bootstrap_samples=500,
        seed=42,
        allow_no_baseline=False,
        allow_missing_bootstrap=False,
        run_metrics={"source_families": ["ru"], "security_scenarios": []},
    )

    assert report["passed"] is True
    assert report["checks"][0]["status"] == "passed"
    assert report["checks"][0]["delta_vs_baseline"] == pytest.approx(0.05, abs=1e-9)


def test_evaluate_gate_fails_without_bootstrap_samples_by_default():
    run_rows = {
        ("ru", "recall_at_10"): {
            "metric_value": 0.9,
            "details": {"sample_size": 120},
        },
    }
    baseline_rows = {
        ("ru", "recall_at_10"): {
            "metric_value": 0.85,
            "details": {"sample_size": 120},
        },
    }

    report = gate.evaluate_gate(
        run_id="run-2",
        run_rows=run_rows,
        baseline_id="base-2",
        baseline_rows=baseline_rows,
        required_slices=["ru"],
        metrics=["recall_at_10"],
        thresholds={"recall_at_10": 0.6},
        min_sample_size=100,
        negative_margin=-0.01,
        bootstrap_samples=300,
        seed=7,
        allow_no_baseline=False,
        allow_missing_bootstrap=False,
        run_metrics={"source_families": ["ru"], "security_scenarios": []},
    )

    assert report["passed"] is False
    assert "missing_bootstrap_samples" in report["checks"][0]["reasons"]


def test_rows_from_status_payload_parses_details_json():
    payload = {
        "run_id": "eval_report_1",
        "status": "completed",
        "results": [
            {
                "slice_name": "ru",
                "metric_name": "recall_at_10",
                "metric_value": 0.81,
                "threshold_value": 0.6,
                "passed": True,
                "details_json": '{"sample_size": 120, "values": [1, 1, 0]}',
            }
        ],
    }

    rows = gate._rows_from_status_payload(payload)

    assert ("ru", "recall_at_10") in rows
    row = rows[("ru", "recall_at_10")]
    assert row["metric_value"] == pytest.approx(0.81, abs=1e-9)
    assert row["details"]["sample_size"] == 120
    assert row["details"]["values"] == [1, 1, 0]


def test_evaluate_gate_with_artifact_rows_passes():
    run_rows = gate._rows_from_status_payload(
        {
            "status": "completed",
            "results": [
                {
                    "slice_name": "ru",
                    "metric_name": "recall_at_10",
                    "metric_value": 0.8,
                    "threshold_value": 0.6,
                    "passed": True,
                    "details": {"sample_size": 150, "values": [1.0] * 150},
                }
            ],
        }
    )
    baseline_rows = gate._rows_from_status_payload(
        {
            "status": "completed",
            "results": [
                {
                    "slice_name": "ru",
                    "metric_name": "recall_at_10",
                    "metric_value": 0.75,
                    "threshold_value": 0.6,
                    "passed": True,
                    "details": {"sample_size": 150, "values": [0.5] * 150},
                }
            ],
        }
    )

    report = gate.evaluate_gate(
        run_id="artifact-run",
        run_rows=run_rows,
        baseline_id="artifact-base",
        baseline_rows=baseline_rows,
        required_slices=["ru"],
        metrics=["recall_at_10"],
        thresholds={"recall_at_10": 0.6},
        min_sample_size=100,
        negative_margin=-0.01,
        bootstrap_samples=400,
        seed=11,
        allow_no_baseline=False,
        allow_missing_bootstrap=False,
        run_metrics={"source_families": ["ru"], "security_scenarios": []},
    )

    assert report["passed"] is True
    assert report["checks"][0]["status"] == "passed"


def test_parse_list_arg_canonicalizes_slice_aliases():
    assert gate._parse_list_arg("long_context, refusal_expected, direct_prompt_injection", []) == [
        "long-context",
        "refusal-expected",
        "direct_injection",
    ]


def test_derive_required_slices_uses_source_family_and_security_metadata():
    metrics = {
        "slices": ["overall", "definition"],
        "source_families": ["pdf", "telegram_chat"],
        "security_scenarios": ["benign", "secret_leak_probe"],
        "failure_modes": ["redact_sensitive"],
        "slice_summary": {
            "overall": {"sample_size": 20},
            "definition": {"sample_size": 0},
            "pdf": {"sample_size": 10},
            "telegram_chat": {"sample_size": 10},
            "secret_leak_probe": {"sample_size": 4},
            "redact_sensitive": {"sample_size": 4},
            "benign": {"sample_size": 16},
        },
    }

    required = gate._derive_required_slices(requested_raw=None, default=["overall"], metrics=metrics)

    assert required == ["overall", "definition", "pdf", "telegram_chat", "benign", "secret_leak_probe", "redact_sensitive"]


def test_evaluate_gate_fails_when_recorded_slice_metric_row_is_missing():
    metrics = {
        "slices": ["overall", "definition"],
        "slice_summary": {
            "overall": {"sample_size": 20},
            "definition": {"sample_size": 0},
        },
    }
    required = gate._derive_required_slices(requested_raw=None, default=["overall"], metrics=metrics)

    report = gate.evaluate_gate(
        run_id="run-4",
        run_rows={("overall", "recall_at_10"): _row(0.9, 20, [1.0] * 20)},
        baseline_id="base-4",
        baseline_rows={("overall", "recall_at_10"): _row(0.8, 20, [1.0] * 16 + [0.0] * 4)},
        required_slices=required,
        metrics=["recall_at_10"],
        thresholds={"recall_at_10": 0.6},
        min_sample_size=10,
        negative_margin=-0.01,
        bootstrap_samples=200,
        seed=21,
        allow_no_baseline=False,
        allow_missing_bootstrap=False,
        run_metrics=metrics,
    )

    assert report["passed"] is False
    definition_check = next(check for check in report["checks"] if check["slice"] == "definition")
    assert definition_check["status"] == "failed"
    assert "missing_metric_row" in definition_check["reasons"]


def test_evaluate_gate_reports_slice_groups():
    run_rows = {
        ("pdf", "recall_at_10"): _row(0.9, 120, [1.0] * 120),
    }
    baseline_rows = {
        ("pdf", "recall_at_10"): _row(0.8, 120, [1.0] * 96 + [0.0] * 24),
    }

    report = gate.evaluate_gate(
        run_id="run-3",
        run_rows=run_rows,
        baseline_id="base-3",
        baseline_rows=baseline_rows,
        required_slices=["pdf"],
        metrics=["recall_at_10"],
        thresholds={"recall_at_10": 0.6},
        min_sample_size=100,
        negative_margin=-0.01,
        bootstrap_samples=300,
        seed=9,
        allow_no_baseline=False,
        allow_missing_bootstrap=False,
        run_metrics={"source_families": ["pdf"], "security_scenarios": ["secret_leak_probe"], "failure_modes": ["redact_sensitive"]},
    )

    assert report["slice_groups"]["source_families"] == ["pdf"]
    assert report["slice_groups"]["security_scenarios"] == []
    assert report["slice_groups"]["failure_modes"] == []


def test_evaluate_gate_prefers_row_threshold_value_for_source_family_slice():
    run_rows = {
        ("telegram_chat", "recall_at_10"): {
            "metric_value": 0.44,
            "threshold_value": 0.45,
            "details": {"sample_size": 120, "values": [1.0] * 53 + [0.0] * 67},
        }
    }
    baseline_rows = {
        ("telegram_chat", "recall_at_10"): _row(0.40, 120, [1.0] * 48 + [0.0] * 72),
    }

    report = gate.evaluate_gate(
        run_id="run-5",
        run_rows=run_rows,
        baseline_id="base-5",
        baseline_rows=baseline_rows,
        required_slices=["telegram_chat"],
        metrics=["recall_at_10"],
        thresholds={"recall_at_10": 0.6},
        min_sample_size=100,
        negative_margin=-0.01,
        bootstrap_samples=200,
        seed=5,
        allow_no_baseline=False,
        allow_missing_bootstrap=False,
        run_metrics={
            "source_families": ["telegram_chat"],
            "security_scenarios": [],
            "failure_modes": [],
            "slice_thresholds": {"telegram_chat": {"recall_at_10": 0.45}},
        },
    )

    assert report["passed"] is False
    assert report["checks"][0]["threshold"] == 0.45
    assert "threshold_not_met" in report["checks"][0]["reasons"]


def test_derive_metrics_uses_run_available_metrics_when_present():
    metrics = {
        "available_metrics": ["recall_at_10", "faithfulness", "citation_validity"],
    }

    derived = gate._derive_metrics(None, gate.DEFAULT_METRICS, metrics)

    assert derived == ["recall_at_10", "faithfulness", "citation_validity"]


def test_evaluate_gate_handles_answer_metric_rows():
    run_rows = {
        ("overall", "faithfulness"): {
            "metric_value": 0.88,
            "threshold_value": 0.80,
            "details": {"sample_size": 120, "values": [0.88] * 120},
        }
    }
    baseline_rows = {
        ("overall", "faithfulness"): {
            "metric_value": 0.80,
            "threshold_value": 0.80,
            "details": {"sample_size": 120, "values": [0.80] * 120},
        }
    }

    report = gate.evaluate_gate(
        run_id="answer-run",
        run_rows=run_rows,
        baseline_id="answer-base",
        baseline_rows=baseline_rows,
        required_slices=["overall"],
        metrics=["faithfulness"],
        thresholds=gate.DEFAULT_THRESHOLDS,
        min_sample_size=100,
        negative_margin=-0.01,
        bootstrap_samples=200,
        seed=17,
        allow_no_baseline=False,
        allow_missing_bootstrap=False,
        run_metrics={"available_metrics": ["faithfulness"]},
    )

    assert report["passed"] is True
    assert report["checks"][0]["metric"] == "faithfulness"
