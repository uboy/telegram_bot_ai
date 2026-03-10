from pathlib import Path
import re

import pytest


REQUIRED_ATTACK_TYPES = {
    "direct_prompt_injection",
    "indirect_prompt_injection",
    "prompt_leak_probe",
    "secret_leak_probe",
    "access_scope_probe",
}


def _load_yaml(path: Path):
    yaml = pytest.importorskip("yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_rag_eval_security_cases_cover_required_attack_types():
    dataset_path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_ready_data_v2.yaml"
    raw = _load_yaml(dataset_path)
    cases = raw.get("test_cases") or []

    attack_cases = [case for case in cases if str(case.get("attack_type") or "").strip() not in {"", "none"}]
    covered = {str(case.get("attack_type") or "").strip() for case in attack_cases}
    assert REQUIRED_ATTACK_TYPES.issubset(covered), f"missing security attack coverage: {REQUIRED_ATTACK_TYPES - covered}"

    for case in attack_cases:
        case_id = str(case.get("id") or "").strip()
        required_flags = case.get("required_flags") or []
        assert str(case.get("expected_answer_mode") or "").strip() == "refusal", (
            f"attack case {case_id} must currently expect refusal behavior"
        )
        assert str(case.get("security_expectation") or "").strip() != "normal", (
            f"attack case {case_id} must declare non-normal security expectation"
        )
        assert isinstance(required_flags, list) and required_flags, f"attack case {case_id} must require flags"


def test_rag_eval_security_cases_reference_adversarial_fixture_when_needed():
    dataset_path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_ready_data_v2.yaml"
    manifest_path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_source_manifest_v1.yaml"
    dataset = _load_yaml(dataset_path)
    manifest = _load_yaml(manifest_path)

    adversarial_fixture_ids = {
        str(fixture.get("fixture_id") or "").strip()
        for fixture in (manifest.get("fixtures") or [])
        if str(fixture.get("screening_profile") or "").strip() == "adversarial_fixture"
    }
    assert adversarial_fixture_ids, "manifest must expose adversarial fixture ids"

    indirect_cases = [
        case for case in (dataset.get("test_cases") or [])
        if str(case.get("attack_type") or "").strip() == "indirect_prompt_injection"
    ]
    assert indirect_cases, "dataset must include indirect prompt injection cases"
    for case in indirect_cases:
        expected_sources = {str(item).strip() for item in (case.get("expected_sources") or []) if str(item).strip()}
        assert expected_sources.intersection(adversarial_fixture_ids), (
            f"indirect injection case {case.get('id')} must reference adversarial fixture id"
        )


def test_rag_eval_security_contract_has_no_raw_local_markers():
    dataset_path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_ready_data_v2.yaml"
    raw_text = dataset_path.read_text(encoding="utf-8")

    forbidden_patterns = [
        r"[A-Za-z]:\\(?:[^\\\r\n]+\\)+[^\\\r\n]+",
        r"/(?:Users|home)/[^/\r\n]+(?:/[^/\r\n]+)+",
        r"ChatExport_[0-9]{4}-[0-9]{2}-[0-9]{2}",
        r"Telegram Desktop[\\/]",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, raw_text) is None, (
            f"security dataset must not embed local corpus marker matching: {pattern}"
        )
