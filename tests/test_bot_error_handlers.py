import pytest

pytest.importorskip("telegram")

from telegram.error import NetworkError

from frontend import error_handlers


class _DummyContext:
    def __init__(self, error):
        self.error = error


@pytest.mark.anyio
async def test_global_error_handler_suppresses_transient_network_disconnect(monkeypatch):
    notifications = []

    async def _notify_admins(message, context):
        notifications.append((message, context))

    monkeypatch.setattr(error_handlers, "notify_admins", _notify_admins)

    error = NetworkError("httpx.RemoteProtocolError: Server disconnected without sending a response.")
    await error_handlers.global_error_handler(object(), _DummyContext(error))

    assert notifications == []


@pytest.mark.anyio
async def test_global_error_handler_notifies_admins_for_non_transient_error(monkeypatch):
    notifications = []

    async def _notify_admins(message, context):
        notifications.append((message, context))

    monkeypatch.setattr(error_handlers, "notify_admins", _notify_admins)

    error = ValueError("boom")
    await error_handlers.global_error_handler(object(), _DummyContext(error))

    assert len(notifications) == 1
    message, _context = notifications[0]
    assert "ValueError: boom" in message


@pytest.mark.anyio
async def test_global_error_handler_notifies_admins_for_non_transient_network_error(monkeypatch):
    notifications = []

    async def _notify_admins(message, context):
        notifications.append((message, context))

    monkeypatch.setattr(error_handlers, "notify_admins", _notify_admins)

    error = NetworkError("proxy handshake failed")
    await error_handlers.global_error_handler(object(), _DummyContext(error))

    assert len(notifications) == 1
    message, _context = notifications[0]
    assert "NetworkError: proxy handshake failed" in message
