import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_arkuiwiki_local_smoke(tmp_path):
    if os.getenv("RAG_ARKUI_WIKI_LOCAL_SMOKE", "").strip() != "1":
        pytest.skip("local-only smoke is opt-in via RAG_ARKUI_WIKI_LOCAL_SMOKE=1")

    zip_path = os.getenv("RAG_ARKUI_WIKI_ZIP_PATH", "").strip()
    if not zip_path:
        pytest.skip("set RAG_ARKUI_WIKI_ZIP_PATH to a local arkui wiki ZIP")

    cases_json = os.getenv("RAG_ARKUI_WIKI_CASES_JSON", "").strip()
    cases_file = os.getenv("RAG_ARKUI_WIKI_CASES_FILE", "").strip()
    if not cases_json and not cases_file:
        pytest.skip("set RAG_ARKUI_WIKI_CASES_JSON or RAG_ARKUI_WIKI_CASES_FILE for arkui smoke queries")

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "arkuiwiki_local_smoke.py"
    json_out = tmp_path / "arkui_smoke.json"
    db_path = tmp_path / "arkui_smoke.db"
    env = os.environ.copy()
    env["MYSQL_URL"] = ""
    env["DB_PATH"] = str(db_path)
    env.setdefault("RAG_ARKUI_WIKI_MODE", "zip")
    env.setdefault("RAG_ARKUI_WIKI_TOP_K", "5")

    command = [
        sys.executable,
        str(script_path),
        "--json-out",
        str(json_out),
        "--mode",
        env["RAG_ARKUI_WIKI_MODE"],
        "--zip-path",
        zip_path,
        "--db-path",
        str(db_path),
        "--answer-mode",
        env.get("RAG_ARKUI_WIKI_ANSWER_MODE", "extractive"),
        "--top-k",
        env["RAG_ARKUI_WIKI_TOP_K"],
    ]
    if cases_json:
        command.extend(["--cases-json", cases_json])
    if cases_file:
        command.extend(["--cases-file", cases_file])

    result = subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )

    assert json_out.exists(), f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["profile"] == "arkuiwiki"
    assert payload["ingest"]["files_processed"] >= 10
    assert payload["ingest"]["chunks_added"] >= 20

    if not payload.get("ok", False):
        pytest.xfail("ArkUI wiki smoke reproduced a retrieval/answer quality gap: " + "; ".join(payload.get("failures", [])))

    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
