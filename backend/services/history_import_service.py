"""Import chat history from exported files."""
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class HistoryImportService:
    """Parses and imports chat history from Telegram exports and CSV/TXT files."""

    def import_history(self, file_content: bytes, chat_id: str,
                       format_hint: str = None,
                       filename: str = None,
                       user_telegram_id: str = None) -> dict:
        """Import history from file. Returns stats dict with import_id."""
        from shared.database import get_session, ChatMessage, ChatImportLog
        from shared.document_loaders.chat_history_parser import auto_detect_and_parse
        from sqlalchemy.exc import IntegrityError

        # Decode file content
        try:
            content = file_content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                content = file_content.decode('utf-8-sig')
            except UnicodeDecodeError:
                content = file_content.decode('latin-1')

        # Create import log entry
        with get_session() as session:
            import_log = ChatImportLog(
                chat_id=chat_id,
                user_telegram_id=user_telegram_id,
                source_filename=filename,
                source_format=format_hint or 'auto',
                status='processing',
            )
            session.add(import_log)
            session.flush()
            import_id = import_log.id

        # Parse messages
        try:
            messages = auto_detect_and_parse(content, chat_id=chat_id,
                                              format_hint=format_hint)
        except Exception as e:
            logger.error("Import parse error: %s", e)
            with get_session() as session:
                log = session.query(ChatImportLog).get(import_id)
                if log:
                    log.status = 'failed'
                    log.error_message = str(e)
            return {"import_id": import_id, "status": "failed",
                    "error": str(e), "messages_imported": 0, "messages_skipped": 0}

        # Batch insert
        imported = 0
        skipped = 0
        BATCH_SIZE = 100

        for i in range(0, len(messages), BATCH_SIZE):
            batch = messages[i:i + BATCH_SIZE]
            with get_session() as session:
                for msg_data in batch:
                    # Parse timestamp
                    ts = msg_data.get('timestamp', '')
                    if isinstance(ts, str) and ts:
                        try:
                            ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        except ValueError:
                            from dateutil import parser as dateutil_parser
                            try:
                                ts = dateutil_parser.parse(ts)
                            except Exception:
                                ts = datetime.now()
                    elif not ts:
                        ts = datetime.now()

                    msg = ChatMessage(
                        chat_id=msg_data.get('chat_id', chat_id),
                        thread_id=msg_data.get('thread_id'),
                        message_id=msg_data.get('message_id', 0),
                        author_telegram_id=msg_data.get('author_telegram_id'),
                        author_username=msg_data.get('author_username'),
                        author_display_name=msg_data.get('author_display_name'),
                        text=msg_data.get('text', ''),
                        timestamp=ts,
                        is_bot_message=msg_data.get('is_bot_message', False),
                        is_system_message=msg_data.get('is_system_message', False),
                        is_imported=True,
                        import_source=filename,
                    )
                    try:
                        session.add(msg)
                        session.flush()
                        imported += 1
                    except IntegrityError:
                        session.rollback()
                        skipped += 1

        # Update import log
        with get_session() as session:
            log = session.query(ChatImportLog).get(import_id)
            if log:
                log.messages_imported = imported
                log.messages_skipped = skipped
                log.status = 'completed'

        logger.info("Import completed: chat_id=%s, imported=%d, skipped=%d",
                     chat_id, imported, skipped)

        return {
            "import_id": import_id,
            "status": "completed",
            "messages_imported": imported,
            "messages_skipped": skipped,
            "messages_found": len(messages),
        }

    def get_import_status(self, import_id: int) -> dict:
        """Get import job status."""
        from shared.database import get_session, ChatImportLog
        with get_session() as session:
            log = session.query(ChatImportLog).get(import_id)
            if not log:
                return None
            return {
                "import_id": log.id,
                "chat_id": log.chat_id,
                "status": log.status,
                "source_filename": log.source_filename,
                "source_format": log.source_format,
                "messages_imported": log.messages_imported,
                "messages_skipped": log.messages_skipped,
                "error_message": log.error_message,
            }
