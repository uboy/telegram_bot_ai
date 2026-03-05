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
    )

    assert report["passed"] is False
    assert "missing_bootstrap_samples" in report["checks"][0]["reasons"]
