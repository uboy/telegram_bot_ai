import pytest

pytest.importorskip("telegram")

from frontend.templates.buttons import admin_menu


def test_admin_menu_does_not_have_global_upload_button():
    markup = admin_menu()
    callbacks = []
    for row in markup.inline_keyboard:
        for button in row:
            callbacks.append(button.callback_data)

    assert "admin_kb" in callbacks
    assert "admin_upload" not in callbacks
