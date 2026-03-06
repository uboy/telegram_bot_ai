from pathlib import Path

import pytest

from backend.services import rag_eval_service as eval_module


REQUIRED_SLICES = {
    "ru",
    "en",
    "mixed",
    "factoid",
    "howto",
    "legal",
    "numeric",
    "long-context",
}


def test_ready_data_eval_corpus_contract():
    yaml = pytest.importorskip("yaml")

    path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_ready_data_v1.yaml"
    assert path.exists(), "ready-data corpus file must exist"

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    assert isinstance(raw, dict)
    cases = raw.get("test_cases")
    assert isinstance(cases, list), "test_cases must be a list"
    assert len(cases) >= 24, "ready-data corpus must contain at least 24 cases"

    ids = []
    covered = set()
    for case in cases:
        assert isinstance(case, dict)
        case_id = str(case.get("id") or "").strip()
        query = str(case.get("query") or "").strip()
        expected_source = str(case.get("expected_source") or "").strip()
        snippets = case.get("expected_snippets") or []

        assert case_id, "each case must have non-empty id"
        assert query, f"case {case_id} must have query"
        assert expected_source, f"case {case_id} must have expected_source"
        assert isinstance(snippets, list) and snippets, f"case {case_id} must have expected_snippets"

        ids.append(case_id)
        covered.update(eval_module._case_slices(case))

    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert REQUIRED_SLICES.issubset(covered), f"missing required slice coverage: {REQUIRED_SLICES - covered}"
