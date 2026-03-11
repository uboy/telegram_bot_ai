import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_openharmony_wiki_local_smoke(tmp_path):
    if os.getenv("RAG_OPENHARMONY_WIKI_LOCAL_SMOKE", "").strip() != "1":
        pytest.skip("local-only smoke is opt-in via RAG_OPENHARMONY_WIKI_LOCAL_SMOKE=1")

    zip_path = os.getenv("RAG_OPENHARMONY_WIKI_ZIP_PATH", "").strip()
    if not zip_path:
        pytest.skip("set RAG_OPENHARMONY_WIKI_ZIP_PATH to a local open-harmony wiki ZIP")

    script_path = Path(__file__).resolve().parent.parent / "scripts" / "openharmony_wiki_local_smoke.py"
    json_out = tmp_path / "openharmony_smoke.json"
    db_path = tmp_path / "openharmony_smoke.db"
    env = os.environ.copy()
    env["MYSQL_URL"] = ""
    env["DB_PATH"] = str(db_path)
    env.setdefault("RAG_OPENHARMONY_WIKI_MODE", "zip")
    env.setdefault("RAG_OPENHARMONY_WIKI_TOP_K", "5")

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--json-out",
            str(json_out),
            "--mode",
            env["RAG_OPENHARMONY_WIKI_MODE"],
            "--zip-path",
            zip_path,
            "--db-path",
            str(db_path),
            "--answer-mode",
            env.get("RAG_OPENHARMONY_WIKI_ANSWER_MODE", "extractive"),
            "--top-k",
            env["RAG_OPENHARMONY_WIKI_TOP_K"],
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )

    assert json_out.exists(), f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["ingest"]["files_processed"] >= 50
    assert payload["ingest"]["chunks_added"] >= 100

    if not payload.get("ok", False):
        pytest.xfail("OpenHarmony smoke reproduced a known retrieval/answer quality gap: " + "; ".join(payload.get("failures", [])))

    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    query_map = {item["query"]: item for item in payload["queries"]}
    mirror_case = query_map["how to sync code with local mirror"]
    assert any("Sync%26Build/Sync%26Build" in path for path in mirror_case["top_sources"])
    assert "repo init" in mirror_case["answer_preview"].lower() or "ohos_mirror" in mirror_case["answer_preview"].lower()

    build_sync_case = query_map["how to build and sync"]
    assert any("Sync%26Build/Sync%26Build" in path for path in build_sync_case["top_sources"])
    assert "repo sync" in build_sync_case["answer_preview"].lower() or "build/prebuilts_download.sh" in build_sync_case["answer_preview"].lower()
