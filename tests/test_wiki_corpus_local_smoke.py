import json
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
        api_key="test-key",
        top_k=4,
    )

    results = smoke._run_remote_queries(
        args=args,
        kb_id=17,
        cases=[{"query": "how to create custom component"}],
    )

    assert captured["url"] == "http://localhost:8000/api/v1/rag/query"
    assert captured["headers"][0]["X-API-Key"] == "test-key"
    assert captured["json"][0]["knowledge_base_id"] == 17
    assert results[0]["top_sources"] == [
        "https://gitee.com/rri_opensource/arkuiwiki/wikis/CustomComponent"
    ]
    assert results[0]["request_id"] == "req-1"
