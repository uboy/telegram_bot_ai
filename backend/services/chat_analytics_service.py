"""Chat analytics orchestration service."""
import logging
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class ChatAnalyticsService:
    """Orchestrates message collection, embedding generation, and digest creation."""

    def store_message(self, payload: dict) -> int:
        """Store a message from Telegram group. Returns ChatMessage.id.

        Handles dedup via IntegrityError on (chat_id, message_id) unique constraint.
        Returns -1 if collection is disabled for the chat.
        """
        from shared.database import get_session, ChatMessage, ChatAnalyticsConfig
        from datetime import datetime
        from sqlalchemy.exc import IntegrityError

        with get_session() as session:
            # Check if collection is enabled (auto-create config if missing)
            config = session.query(ChatAnalyticsConfig).filter_by(
                chat_id=payload['chat_id']).first()
            if config and not config.collection_enabled:
                return -1  # Collection disabled

            # Parse timestamp
            ts = payload.get('timestamp')
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))

            msg = ChatMessage(
                chat_id=payload['chat_id'],
                thread_id=payload.get('thread_id'),
                message_id=payload['message_id'],
                author_telegram_id=payload.get('author_telegram_id'),
                author_username=payload.get('author_username'),
                author_display_name=payload.get('author_display_name'),
                text=payload.get('text'),
                message_link=payload.get('message_link'),
                timestamp=ts,
                is_bot_message=payload.get('is_bot_message', False),
                is_system_message=payload.get('is_system_message', False),
            )
            try:
                session.add(msg)
                session.flush()
                return msg.id
            except IntegrityError:
                session.rollback()
                # Duplicate - return existing
                existing = session.query(ChatMessage).filter_by(
                    chat_id=payload['chat_id'],
                    message_id=payload['message_id']).first()
                return existing.id if existing else -1

    def get_config(self, chat_id: str):
        """Get analytics config for a chat."""
        from shared.database import get_session, ChatAnalyticsConfig
        with get_session() as session:
            config = session.query(ChatAnalyticsConfig).filter_by(chat_id=chat_id).first()
            return config

    def list_configs(self) -> list:
        """List all analytics configs."""
        from shared.database import get_session, ChatAnalyticsConfig
        with get_session() as session:
            return session.query(ChatAnalyticsConfig).all()

    def upsert_config(self, chat_id: str, data: dict):
        """Create or update analytics config."""
        from shared.database import get_session, ChatAnalyticsConfig
        with get_session() as session:
            config = session.query(ChatAnalyticsConfig).filter_by(chat_id=chat_id).first()
            if not config:
                config = ChatAnalyticsConfig(chat_id=chat_id)
                session.add(config)
            for key, value in data.items():
                if value is not None and hasattr(config, key):
                    setattr(config, key, value)
            session.flush()
            return config

    def delete_config(self, chat_id: str):
        """Delete analytics config for a chat."""
        from shared.database import get_session, ChatAnalyticsConfig
        with get_session() as session:
            config = session.query(ChatAnalyticsConfig).filter_by(chat_id=chat_id).first()
            if config:
                session.delete(config)
                return True
            return False

    async def run_analysis(self, chat_id: str, period_start: datetime,
                           period_end: datetime) -> int:
        """Run full analysis pipeline: embed -> cluster -> extract themes -> generate digest.

        Returns digest_id.
        """
        import json
        import time
        import asyncio
        from shared.database import (get_session, ChatMessage, ChatDigest,
                                     ChatDigestTheme)
        from shared.chat_analytics_rag import chat_analytics_rag
        from shared.config import (ANALYTICS_CLUSTER_METHOD, ANALYTICS_CLUSTER_MIN_SIZE,
                                   ANALYTICS_MAX_THEMES, ANALYTICS_DIGEST_MAX_MESSAGES)
        from backend.services.theme_clustering_service import ThemeClusteringService
        from backend.services.digest_generator_service import DigestGeneratorService

        start_time = time.time()

        # 1. Create digest record (pending)
        with get_session() as session:
            digest = ChatDigest(
                chat_id=chat_id,
                period_start=period_start,
                period_end=period_end,
                status='generating',
            )
            session.add(digest)
            session.flush()
            digest_id = digest.id

        try:
            # 2. Load messages for the period
            with get_session() as session:
                messages = session.query(ChatMessage).filter(
                    ChatMessage.chat_id == chat_id,
                    ChatMessage.timestamp >= period_start,
                    ChatMessage.timestamp <= period_end,
                    ChatMessage.is_bot_message == False,
                    ChatMessage.is_system_message == False,
                    ChatMessage.text.isnot(None),
                ).order_by(ChatMessage.timestamp).limit(
                    ANALYTICS_DIGEST_MAX_MESSAGES
                ).all()

                if not messages:
                    with get_session() as s2:
                        d = s2.query(ChatDigest).get(digest_id)
                        d.status = 'failed'
                        d.error_message = 'No messages found for the period'
                    return digest_id

                msg_dicts = [{
                    'message_id': m.message_id,
                    'text': m.text,
                    'author_telegram_id': m.author_telegram_id,
                    'author_display_name': m.author_display_name or m.author_username or 'Unknown',
                    'timestamp': m.timestamp,
                    'thread_id': m.thread_id,
                    'message_link': m.message_link,
                } for m in messages]

            # 3. Preprocess & embed
            clustering_svc = ThemeClusteringService()
            blocks = clustering_svc.preprocess_messages(msg_dicts)

            if not blocks:
                with get_session() as s2:
                    d = s2.query(ChatDigest).get(digest_id)
                    d.status = 'failed'
                    d.error_message = 'Not enough meaningful messages for analysis'
                return digest_id

            texts = [b['text'] for b in blocks]
            embeddings = chat_analytics_rag.embed_messages(texts)

            # 4. Cluster
            labels = clustering_svc.cluster_messages(
                embeddings,
                min_cluster_size=ANALYTICS_CLUSTER_MIN_SIZE,
                method=ANALYTICS_CLUSTER_METHOD,
            )

            # 5. Group blocks by cluster
            from collections import defaultdict
            clusters = defaultdict(list)
            for block, label in zip(blocks, labels):
                if label >= 0:  # skip noise
                    clusters[int(label)].append(block)

            # 6. Extract themes (parallel LLM calls, capped)
            theme_tasks = []
            for cid, cluster_blocks in sorted(clusters.items())[:ANALYTICS_MAX_THEMES]:
                theme_tasks.append(clustering_svc.extract_theme(cluster_blocks, cid))

            themes = await asyncio.gather(*theme_tasks)

            # 7. Compute stats
            unique_authors = len({m['author_telegram_id'] for m in msg_dicts if m.get('author_telegram_id')})
            active_threads = len({m['thread_id'] for m in msg_dicts if m.get('thread_id')})
            stats = {
                'total_messages': len(msg_dicts),
                'unique_participants': unique_authors,
                'active_threads': active_threads,
            }

            # 8. Generate digest HTML
            digest_svc = DigestGeneratorService()
            digest_html = await digest_svc.generate_digest_text(
                themes, period_start, period_end, stats)

            # 9. Save digest + themes
            elapsed = int(time.time() - start_time)
            with get_session() as session:
                d = session.query(ChatDigest).get(digest_id)
                d.summary_text = digest_html
                d.theme_count = len(themes)
                d.total_messages_analyzed = len(msg_dicts)
                d.generation_time_sec = elapsed
                d.status = 'completed'

                for i, theme in enumerate(themes):
                    t = ChatDigestTheme(
                        digest_id=digest_id,
                        emoji=theme.get('emoji'),
                        title=theme.get('title', ''),
                        summary=theme.get('summary', ''),
                        related_thread_ids=json.dumps(
                            list({b.get('thread_id') for b in clusters.get(i, []) if b.get('thread_id')})),
                        key_message_links=json.dumps(theme.get('key_message_links', [])),
                        main_participants=json.dumps(theme.get('main_participants', [])),
                        message_count=theme.get('message_count', 0),
                        sort_order=i,
                    )
                    session.add(t)

            logger.info("Analysis complete: chat_id=%s, digest_id=%d, themes=%d, time=%ds",
                        chat_id, digest_id, len(themes), elapsed)
            return digest_id

        except Exception as e:
            logger.error("Analysis failed for chat_id=%s: %s", chat_id, e, exc_info=True)
            with get_session() as session:
                d = session.query(ChatDigest).get(digest_id)
                if d:
                    d.status = 'failed'
                    d.error_message = str(e)[:500]
            return digest_id

    def generate_embeddings_for_messages(self, chat_id: str,
                                         message_ids: List[int] = None):
        """Generate embeddings for messages in batch and store in DB."""
        import json
        from shared.database import get_session, ChatMessage, ChatMessageEmbedding
        from shared.chat_analytics_rag import chat_analytics_rag
        from shared.config import ANALYTICS_MIN_TEXT_LENGTH, ANALYTICS_EMBEDDING_BATCH_SIZE

        with get_session() as session:
            query = session.query(ChatMessage).filter(
                ChatMessage.chat_id == chat_id,
                ChatMessage.text.isnot(None),
            )
            if message_ids:
                query = query.filter(ChatMessage.id.in_(message_ids))

            messages = query.all()

            # Filter by min text length and exclude already embedded
            existing_ids = {e.chat_message_id for e in
                           session.query(ChatMessageEmbedding.chat_message_id).filter(
                               ChatMessageEmbedding.chat_message_id.in_([m.id for m in messages])
                           ).all()} if messages else set()

            to_embed = [m for m in messages
                        if m.id not in existing_ids
                        and m.text and len(m.text) >= ANALYTICS_MIN_TEXT_LENGTH]

            if not to_embed:
                logger.info("No new messages to embed for chat_id=%s", chat_id)
                return 0

            # Batch embed
            texts = [m.text for m in to_embed]
            embeddings = chat_analytics_rag.embed_messages(texts)

            # Store in DB
            count = 0
            for msg, emb in zip(to_embed, embeddings):
                rec = ChatMessageEmbedding(
                    chat_message_id=msg.id,
                    embedding=json.dumps(emb.tolist()),
                    model_name=str(chat_analytics_rag._encoder.__class__.__name__
                                   if chat_analytics_rag._encoder else 'unknown'),
                )
                session.add(rec)
                count += 1

            logger.info("Generated %d embeddings for chat_id=%s", count, chat_id)
            return count

    def cleanup_old_messages(self, chat_id: str, retention_days: int):
        """Delete messages older than retention_days."""
        from datetime import timedelta, timezone
        from shared.database import get_session, ChatMessage, ChatMessageEmbedding

        if retention_days <= 0:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        with get_session() as session:
            old_messages = session.query(ChatMessage).filter(
                ChatMessage.chat_id == chat_id,
                ChatMessage.timestamp < cutoff,
            ).all()

            if not old_messages:
                return 0

            old_ids = [m.id for m in old_messages]

            # Delete embeddings first (FK constraint)
            session.query(ChatMessageEmbedding).filter(
                ChatMessageEmbedding.chat_message_id.in_(old_ids)
            ).delete(synchronize_session=False)

            # Delete messages
            deleted = session.query(ChatMessage).filter(
                ChatMessage.id.in_(old_ids)
            ).delete(synchronize_session=False)

            logger.info("Cleaned up %d old messages for chat_id=%s (older than %d days)",
                        deleted, chat_id, retention_days)
            return deleted
