import json

from scripts import rag_orchestrator_compare as compare


def test_load_cases_from_json(tmp_path):
    payload = [
        {
            "id": "c1",
            "query": "What is sync build?",
            "expected_source": "Sync&Build.md",
            "expected_snippets": ["repo init", "repo sync"],
        }
    ]
    file_path = tmp_path / "cases.json"
    file_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    cases = compare._load_cases(str(file_path))
    assert len(cases) == 1
    assert cases[0].case_id == "c1"
    assert cases[0].expected_source == "Sync&Build.md"
    assert cases[0].expected_snippets == ["repo init", "repo sync"]


def test_snippet_match_count_supports_regex_and_plain_text():
    answer = "Run repo init -b master, then repo sync -c -j 8."
    patterns = ["repo init", r"repo\s+sync\s+-c\s+-j\s+8", "missing-token"]

    hits = compare._snippet_match_count(answer, patterns)
    assert hits == 2


def test_summarize_rates():
    rows = [
        {
            "ok": True,
            "answer_len": 10,
            "source_hit": True,
            "snippet_hits": 2,
            "snippet_total": 2,
            "degraded_mode": False,
        },
        {
            "ok": True,
            "answer_len": 0,
            "source_hit": False,
            "snippet_hits": 0,
            "snippet_total": 1,
            "degraded_mode": True,
        },
    ]

    summary = compare._summarize(rows)
    assert summary["cases_total"] == 2
    assert summary["ok_rate"] == 1.0
    assert summary["non_empty_rate"] == 0.5
    assert summary["source_hit_rate"] == 0.5
    assert summary["snippet_hit_rate"] == 2 / 3
    assert summary["degraded_mode_rate"] == 0.5
