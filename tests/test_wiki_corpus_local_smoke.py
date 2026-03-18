import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from scripts import wiki_corpus_local_smoke as smoke


def test_profile_config_exposes_openharmony_and_arkuiwiki():
    openharmony = smoke._profile_config("openharmony")
    arkui = smoke._profile_config("arkuiwiki")

    assert openharmony["env_prefix"] == "RAG_OPENHARMONY_WIKI_"
    assert arkui["env_prefix"] == "RAG_ARKUI_WIKI_"
    assert str(arkui["wiki_url"]).endswith("/arkuiwiki/wikis")


def test_load_cases_uses_profile_defaults_when_no_override():
    cases = smoke._load_cases("openharmony")

    assert len(cases) >= 2
    assert any(case["query"] == "how to build and sync" for case in cases)


def test_load_cases_accepts_json_override():
    cases = smoke._load_cases(
        "arkuiwiki",
        cases_json=json.dumps(
            [
                {
                    "query": "how to create custom component",
                    "expected_source_fragment": "CustomComponent",
                    "expected_answer_fragments": ["@Component"],
                }
            ],
            ensure_ascii=False,
        ),
    )

    assert cases == [
        {
            "query": "how to create custom component",
            "expected_source_fragment": "CustomComponent",
            "expected_answer_fragments": ["@Component"],
        }
    ]


def test_run_remote_queries_uses_backend_api_key(monkeypatch):
    captured = {"headers": [], "json": []}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "answer": "Use @Component and @Entry.",
                "request_id": "req-1",
                "sources": [
                    {
                        "source_path": "https://gitee.com/rri_opensource/arkuiwiki/wikis/CustomComponent",
                        "section_title": "Custom Component",
                    }
                ],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def post(self, url, headers=None, json=None):  # noqa: A002
            captured["url"] = url
            captured["headers"].append(dict(headers or {}))
            captured["json"].append(dict(json or {}))
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)

    args = SimpleNamespace(
        backend_url="http://localhost:8000",
        api_key="example-test-token",
        top_k=4,
    )

    results = smoke._run_remote_queries(
        args=args,
        kb_id=17,
        cases=[{"query": "how to create custom component"}],
    )

    assert captured["url"] == "http://localhost:8000/api/v1/rag/query"
    assert captured["headers"][0]["X-API-Key"] == "example-test-token"
    assert captured["json"][0]["knowledge_base_id"] == 17
    assert results[0]["top_sources"] == [
        "https://gitee.com/rri_opensource/arkuiwiki/wikis/CustomComponent"
    ]
    assert results[0]["request_id"] == "req-1"


def test_emit_payload_falls_back_to_utf8_buffer(monkeypatch):
    captured = BytesIO()

    class FakeStdout:
        def __init__(self):
            self.buffer = captured

        def write(self, _text):
            raise UnicodeEncodeError("cp1251", "т", 0, 1, "boom")

    monkeypatch.setattr(smoke.sys, "stdout", FakeStdout())

    smoke._emit_payload('{"message":"тест"}')

    assert captured.getvalue().decode("utf-8") == '{"message":"тест"}\n'


def test_materialize_zip_input_builds_temp_archive_from_directory(tmp_path):
    wiki_root = tmp_path / "wiki"
    page_dir = wiki_root / "Guide"
    page_dir.mkdir(parents=True)
    (page_dir / "Setup.md").write_text("# Setup\nrepo sync\n", encoding="utf-8")

    args = SimpleNamespace(
        mode="dir",
        dir_path=str(wiki_root),
        zip_path="",
        profile="arkuiwiki",
    )

    zip_path, temp_dir = smoke._materialize_zip_input(args)
    try:
        archive = Path(zip_path)
        assert archive.exists()
        with zipfile.ZipFile(archive) as zf:
            assert "Guide/Setup.md" in zf.namelist()
            assert zf.read("Guide/Setup.md").decode("utf-8").startswith("# Setup")
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
