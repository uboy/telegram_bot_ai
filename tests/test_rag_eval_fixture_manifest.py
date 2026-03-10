from pathlib import Path
import re

import pytest


ALLOWED_PATH_MODES = {"repo_relative", "env_override", "generated_local_cache"}
ALLOWED_SENSITIVITY = {"public", "internal", "confidential"}
ALLOWED_SCREENING = {"default", "strict_private", "adversarial_fixture"}
ALLOWED_INGEST_KIND = {"document", "code_path", "chat_export"}


def test_rag_eval_source_manifest_contract():
    yaml = pytest.importorskip("yaml")

    path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_source_manifest_v1.yaml"
    assert path.exists(), "source manifest file must exist"

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    assert str(raw.get("source_manifest_version") or "").strip() == "rag_eval_source_manifest_v1"
    fixtures = raw.get("fixtures")
    assert isinstance(fixtures, list) and fixtures, "fixtures must be a non-empty list"

    fixture_ids = []
    adversarial_count = 0
    for fixture in fixtures:
        assert isinstance(fixture, dict)
        fixture_id = str(fixture.get("fixture_id") or "").strip()
        path_mode = str(fixture.get("path_mode") or "").strip()
        default_path = str(fixture.get("default_path") or "").strip()
        commit_allowed = bool(fixture.get("commit_allowed"))
        sensitivity = str(fixture.get("sensitivity") or "").strip()
        screening_profile = str(fixture.get("screening_profile") or "").strip()
        ingest_kind = str(fixture.get("ingest_kind") or "").strip()

        assert fixture_id, "fixture_id must be non-empty"
        assert path_mode in ALLOWED_PATH_MODES
        assert default_path, f"fixture {fixture_id} must define default_path"
        assert sensitivity in ALLOWED_SENSITIVITY
        assert screening_profile in ALLOWED_SCREENING
        assert ingest_kind in ALLOWED_INGEST_KIND

        if path_mode == "repo_relative" and commit_allowed:
            assert (Path(__file__).resolve().parents[1] / default_path).exists(), (
                f"repo fixture path must exist for {fixture_id}"
            )
        if path_mode == "env_override":
            assert default_path.startswith("${RAG_EVAL_"), f"env_override fixture {fixture_id} must use env placeholder"
            assert commit_allowed is False, f"env_override fixture {fixture_id} must stay local-only"
        if sensitivity == "confidential":
            assert commit_allowed is False, f"confidential fixture {fixture_id} must not be commit-allowed"
        if screening_profile == "adversarial_fixture":
            adversarial_count += 1

        fixture_ids.append(fixture_id)

    assert len(fixture_ids) == len(set(fixture_ids)), "fixture ids must be unique"
    assert adversarial_count >= 1, "manifest must include at least one adversarial fixture"


def test_rag_eval_source_manifest_has_no_local_absolute_paths():
    path = Path(__file__).resolve().parents[1] / "tests" / "data" / "rag_eval_source_manifest_v1.yaml"
    raw_text = path.read_text(encoding="utf-8")

    forbidden_patterns = [
        r"[A-Za-z]:\\(?:[^\\\r\n]+\\)+[^\\\r\n]+",
        r"/(?:Users|home)/[^/\r\n]+(?:/[^/\r\n]+)+",
        r"ChatExport_[0-9]{4}-[0-9]{2}-[0-9]{2}",
        r"Telegram Desktop[\\/]",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, raw_text) is None, f"manifest must not embed local marker matching: {pattern}"
