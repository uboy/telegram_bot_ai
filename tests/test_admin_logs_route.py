import pytest

pytest.importorskip("fastapi")

from backend.api.routes import knowledge as knowledge_routes


def test_get_admin_logs_returns_bounded_redacted_entries(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "bot.log").write_text(
        "first line\nAuthorization: Bearer secret-token\nmysql://user:pass@example.com/db\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_LOG_DIR", str(log_dir))

    response = knowledge_routes.get_admin_logs(service="all", tail_lines=2)

    assert len(response.entries) == 2
    assert response.entries[0].service == "bot"
    assert "Bearer ***" in response.entries[0].line
    assert "secret-token" not in response.entries[0].line
    assert "***:***@" in response.entries[1].line


def test_get_admin_logs_filters_by_service(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "bot.log").write_text("bot line\n", encoding="utf-8")
    (log_dir / "worker.log").write_text("worker line\n", encoding="utf-8")
    monkeypatch.setenv("BOT_LOG_DIR", str(log_dir))

    response = knowledge_routes.get_admin_logs(service="worker", tail_lines=10)

    assert [entry.service for entry in response.entries] == ["worker"]
