# Design Spec: ASR Metadata Visibility and Formatting (v1)

## Context
Users currently receive a large block of technical information (filename, duration, size, etc.) with every transcription result. This information is often redundant and clutters the chat interface.

## Goals
- Allow users to toggle technical metadata display on/off.
- Allow admins to set a global default for all users.
- Improve the visual presentation of metadata using Telegram-native formatting.

## Proposed Solution

### Database Schema Changes
- Add `show_asr_metadata` (Boolean, default: True) to the `users` table.
- Add `show_asr_metadata` (Boolean, default: True) to the `app_settings` table.

### Backend API Changes
- Update `AsrSettings` and `AsrSettingsUpdate` schemas to include `show_asr_metadata`.
- Update `/asr/settings` (GET/PUT) to manage the global flag.

### Frontend (Bot) Changes
- **Settings Menu:** Add a toggle button "🎙️ Тех. инфо в ASR: [ВКЛ/ВЫКЛ]".
- **Admin ASR Menu:** Add a global toggle for metadata display.
- **Result Formatting:** 
    - Wrap technical metadata in `<blockquote expandable>` for Telegram HTML.
    - Check both user-level and (optionally) global-level flags before rendering.

## Implementation Details
- **Migration:** Automatic `ALTER TABLE` in `shared/database.py`.
- **User Context:** Added to `UserContext` dataclass in `shared/types.py` for efficient handler access.
- **Handlers:** `handle_voice` and `handle_audio` in `frontend/bot_handlers.py` now use the flag and HTML formatting.

## Acceptance Criteria
- [x] User can disable ASR metadata in their settings.
- [x] Admin can toggle global default for ASR metadata.
- [x] Metadata is displayed as an expandable block in Telegram.
- [x] Transcription text is separated from technical metadata.
- [x] Migration successfully adds new columns to SQLite/MySQL.

## Risks
- **Telegram Client Support:** Older clients may not support `expandable` quotes; they will see a regular quote block.
- **HTML Escaping:** Special characters in filenames must be escaped to avoid breaking Telegram HTML. (Resolved via `html.escape`).
