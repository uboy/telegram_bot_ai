from pathlib import Path
import re

import pytest

from backend.services import rag_eval_service as eval_module


REQUIRED_LANGUAGE_SLICES = {
    "ru",
    "en",
    "mixed",
}
REQUIRED_QUERY_SLICES = {
    "factoid",
    "howto",
    "definition",
    "legal",
    "numeric",
    "long-context",
}
REQUIRED_MULTICORPUS_QUERY_SLICES = {
    "howto",
    "definition",
    "navigation",
    "troubleshooting",
}
REQUIRED_SOURCE_FAMILIES = {
    "pdf",
    "open_harmony_docs",
    "open_harmony_code",
    "telegram_chat",
}


def test_ready_data_eval_corpus_contract():
    yaml = pytest.importorskip("yaml")

    path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_ready_data_v2.yaml"
    assert path.exists(), "ready-data corpus file must exist"

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    assert isinstance(raw, dict)
    assert str(raw.get("dataset_version") or "").strip() == "rag_eval_ready_data_v2"
    cases = raw.get("test_cases")
    assert isinstance(cases, list), "test_cases must be a list"
    assert len(cases) >= 16, "ready-data corpus must contain at least 16 cases"

    ids = []
    covered_languages = set()
    covered_queries = set()
    covered_source_families = set()
    for case in cases:
        assert isinstance(case, dict)
        case_id = str(case.get("id") or "").strip()
        query = str(case.get("query") or "").strip()
        source_family = str(case.get("source_family") or "").strip()
        expected_sources = case.get("expected_sources") or []
        snippets = case.get("expected_snippets") or []
        expected_answer_mode = str(case.get("expected_answer_mode") or "").strip()
        security_expectation = str(case.get("security_expectation") or "").strip()
        tags = case.get("tags") or []

        assert case_id, "each case must have non-empty id"
        assert query, f"case {case_id} must have query"
        assert source_family in REQUIRED_SOURCE_FAMILIES, f"case {case_id} has unsupported source_family"
        assert isinstance(expected_sources, list) and expected_sources, f"case {case_id} must have expected_sources"
        assert isinstance(snippets, list) and snippets, f"case {case_id} must have expected_snippets"
        assert expected_answer_mode in {"grounded_answer", "refusal"}, f"case {case_id} invalid expected_answer_mode"
        assert security_expectation in {
            "normal",
            "refuse_injection",
            "refuse_prompt_leak",
            "redact_sensitive",
            "flag_poisoned_context",
        }, f"case {case_id} invalid security_expectation"
        assert isinstance(tags, list) and tags, f"case {case_id} must have tags"

        ids.append(case_id)
        slices = eval_module._case_slices(case)
        covered_languages.update(REQUIRED_LANGUAGE_SLICES.intersection(slices))
        covered_queries.update(REQUIRED_QUERY_SLICES.intersection(slices))
        covered_source_families.update(REQUIRED_SOURCE_FAMILIES.intersection(slices))

    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert REQUIRED_LANGUAGE_SLICES.issubset(covered_languages), (
        f"missing required language coverage: {REQUIRED_LANGUAGE_SLICES - covered_languages}"
    )
    assert REQUIRED_QUERY_SLICES.issubset(covered_queries), (
        f"missing required query coverage: {REQUIRED_QUERY_SLICES - covered_queries}"
    )
    assert REQUIRED_SOURCE_FAMILIES.issubset(covered_source_families), (
        f"missing required source-family coverage: {REQUIRED_SOURCE_FAMILIES - covered_source_families}"
    )


def test_ready_data_eval_corpus_has_no_local_paths_or_raw_export_strings():
    path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_ready_data_v2.yaml"
    raw_text = path.read_text(encoding="utf-8")

    forbidden_patterns = [
        r"[A-Za-z]:\\(?:[^\\\r\n]+\\)+[^\\\r\n]+",
        r"/(?:Users|home)/[^/\r\n]+(?:/[^/\r\n]+)+",
        r"ChatExport_[0-9]{4}-[0-9]{2}-[0-9]{2}",
        r"Telegram Desktop[\\/]",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, raw_text) is None, (
            f"committed eval dataset must not embed local corpus marker matching: {pattern}"
        )


def test_multicorpus_public_eval_suite_contract():
    yaml = pytest.importorskip("yaml")

    path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_multicorpus_public_v1.yaml"
    assert path.exists(), "multi-corpus public suite file must exist"

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    assert isinstance(raw, dict)
    assert str(raw.get("dataset_version") or "").strip() == "rag_eval_multicorpus_public_v1"
    cases = raw.get("test_cases")
    assert isinstance(cases, list) and len(cases) >= 7

    source_families = {str(case.get("source_family") or "").strip() for case in cases if isinstance(case, dict)}
    assert {"open_harmony_docs", "arkuiwiki_docs"} <= source_families

    ids = [str(case.get("id") or "").strip() for case in cases if isinstance(case, dict)]
    assert len(ids) == len(set(ids))

    query_slices = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        query_slices.update(REQUIRED_MULTICORPUS_QUERY_SLICES.intersection(eval_module._case_slices(case)))
    assert REQUIRED_MULTICORPUS_QUERY_SLICES.issubset(query_slices)

    raw_text = path.read_text(encoding="utf-8")
    forbidden_patterns = [
        r"[A-Za-z]:\\(?:[^\\\r\n]+\\)+[^\\\r\n]+",
        r"/(?:Users|home)/[^/\r\n]+(?:/[^/\r\n]+)+",
        r"ChatExport_[0-9]{4}-[0-9]{2}-[0-9]{2}",
        r"Telegram Desktop[\\/]",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, raw_text) is None
