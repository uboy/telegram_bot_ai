import json

from shared.qdrant_backend import QdrantBackend


class DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload


def test_qdrant_search_applies_path_prefix_filter(monkeypatch):
    captured = {}

    def fake_request(method, url, json=None, headers=None, timeout=0):  # noqa: ARG001
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = json
        return DummyResponse(
            200,
            {
                "result": [
                    {
                        "id": 1,
                        "score": 0.88,
                        "payload": {"source_path": "doc://policy/25", "source_type": "pdf"},
                    },
                    {
                        "id": 2,
                        "score": 0.87,
                        "payload": {"source_path": "doc://other/10", "source_type": "pdf"},
                    },
                ]
            },
        )

    monkeypatch.setattr("shared.qdrant_backend.requests.request", fake_request)
    backend = QdrantBackend(url="http://qdrant:6333", collection="kb_chunks")

    rows = backend.search(vector=[0.1, 0.2], limit=5, kb_id=1, path_prefixes=["doc://policy"])

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/collections/kb_chunks/points/search")
    assert captured["payload"]["filter"]["must"][0]["match"]["value"] == 1
    assert len(rows) == 1
    assert rows[0].point_id == 1


def test_qdrant_ensure_collection_creates_on_missing(monkeypatch):
    calls = []

    def fake_request(method, url, json=None, headers=None, timeout=0):  # noqa: ARG001
        calls.append((method, url, json))
        if method.upper() == "GET":
            return DummyResponse(404, {"status": "not found"})
        return DummyResponse(200, {"status": "ok"})

    monkeypatch.setattr("shared.qdrant_backend.requests.request", fake_request)
    backend = QdrantBackend(url="http://qdrant:6333", collection="kb_chunks")

    backend.ensure_collection(384)

    assert len(calls) == 2
    assert calls[0][0].upper() == "GET"
    assert calls[1][0].upper() == "PUT"
    assert calls[1][2]["vectors"]["size"] == 384


def test_qdrant_ensure_collection_uses_cached_vector_size(monkeypatch):
    calls = []

    def fake_request(method, url, json=None, headers=None, timeout=0):  # noqa: ARG001
        calls.append((method, url, json))
        return DummyResponse(
            200,
            {
                "result": {
                    "config": {"params": {"vectors": {"size": 768}}},
                }
            },
        )

    monkeypatch.setattr("shared.qdrant_backend.requests.request", fake_request)
    backend = QdrantBackend(url="http://qdrant:6333", collection="kb_chunks")

    backend.ensure_collection(768)
    backend.ensure_collection(768)

    assert len(calls) == 1
    assert calls[0][0].upper() == "GET"
