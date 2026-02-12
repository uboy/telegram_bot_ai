"""Full-text and semantic search over chat message history."""
import asyncio
import logging
from typing import Optional, List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class ChatSearchService:
    """Hybrid search (semantic + BM25) and Q&A over chat messages."""

    def search(self, query: str, chat_id: str,
               filters: dict = None, top_k: int = 10) -> list:
        """Hybrid search over chat messages.

        Args:
            query: Search query
            chat_id: Chat ID to search in
            filters: Optional filters (thread_id, author_telegram_id, period_start, period_end)
            top_k: Number of results to return

        Returns:
            List of dicts with message data, context, and scores
        """
        try:
            from shared.chat_analytics_rag import chat_analytics_rag
            from shared.database import get_session, ChatMessage

            # Ensure index exists, build if needed
            if chat_id not in chat_analytics_rag.faiss_indices:
                logger.info("Chat index not found for %s, building...", chat_id)
                self._build_index_from_db(chat_id)

            # Perform hybrid search using chat_analytics_rag
            search_results = chat_analytics_rag.search(query, chat_id, top_k, filters)

            # For each result, fetch context (1 message before and after)
            results_with_context = []
            with get_session() as session:
                for result in search_results:
                    message_id = result.get('message_id')
                    if not message_id:
                        continue

                    # Get the message from DB
                    msg = session.query(ChatMessage).filter(
                        ChatMessage.chat_id == chat_id,
                        ChatMessage.message_id == message_id
                    ).first()

                    if not msg:
                        continue

                    # Fetch context: 1 message before and 1 after by timestamp
                    context_before = session.query(ChatMessage).filter(
                        ChatMessage.chat_id == chat_id,
                        ChatMessage.timestamp < msg.timestamp
                    ).order_by(ChatMessage.timestamp.desc()).first()

                    context_after = session.query(ChatMessage).filter(
                        ChatMessage.chat_id == chat_id,
                        ChatMessage.timestamp > msg.timestamp
                    ).order_by(ChatMessage.timestamp.asc()).first()

                    # Build result item
                    result_item = {
                        'message_id': msg.message_id,
                        'text': msg.text or '',
                        'author': msg.author_username or msg.author_display_name or 'Unknown',
                        'timestamp': msg.timestamp.isoformat() if msg.timestamp else '',
                        'message_link': msg.message_link,
                        'thread_id': msg.thread_id,
                        'context_before': context_before.text if context_before else None,
                        'context_after': context_after.text if context_after else None,
                        'score': result.get('score', 0.0)
                    }
                    results_with_context.append(result_item)

            return results_with_context

        except Exception as e:
            logger.error("Search failed for chat_id=%s: %s", chat_id, e, exc_info=True)
            return []

    def _build_index_from_db(self, chat_id: str) -> None:
        """Load messages from DB and build FAISS index for a chat."""
        try:
            from shared.database import get_session, ChatMessage, ChatMessageEmbedding
            from shared.chat_analytics_rag import chat_analytics_rag
            import numpy as np
            import json

            with get_session() as session:
                # Query all messages for this chat
                messages = session.query(ChatMessage).filter(
                    ChatMessage.chat_id == chat_id,
                    ChatMessage.is_bot_message == False,
                    ChatMessage.is_system_message == False,
                    ChatMessage.text.isnot(None)
                ).order_by(ChatMessage.timestamp).all()

                if not messages:
                    logger.warning("No messages found for chat_id=%s", chat_id)
                    return

                # Get embeddings from DB
                message_ids = [msg.id for msg in messages]
                embeddings_records = session.query(ChatMessageEmbedding).filter(
                    ChatMessageEmbedding.chat_message_id.in_(message_ids)
                ).all()

                # Build embedding map
                embedding_map = {}
                for rec in embeddings_records:
                    try:
                        emb = json.loads(rec.embedding)
                        embedding_map[rec.chat_message_id] = np.array(emb, dtype='float32')
                    except Exception as e:
                        logger.warning("Failed to parse embedding for message %s: %s", rec.chat_message_id, e)

                # Filter messages that have embeddings
                messages_with_emb = []
                embeddings_list = []
                for msg in messages:
                    if msg.id in embedding_map:
                        messages_with_emb.append(msg)
                        embeddings_list.append(embedding_map[msg.id])

                if not embeddings_list:
                    logger.warning("No embeddings found for chat_id=%s", chat_id)
                    return

                # Stack embeddings
                embeddings = np.vstack(embeddings_list)

                # Build index
                chat_analytics_rag.build_index(chat_id, messages_with_emb, embeddings)
                logger.info("Built index for chat_id=%s with %d messages", chat_id, len(messages_with_emb))

        except Exception as e:
            logger.error("Failed to build index for chat_id=%s: %s", chat_id, e, exc_info=True)

    async def answer_question(self, question: str, chat_id: str,
                              filters: dict = None) -> dict:
        """Answer a question using chat history as context.

        Args:
            question: Question to answer
            chat_id: Chat ID to search in
            filters: Optional filters

        Returns:
            Dict with answer, sources, and confidence level
        """
        try:
            from shared.ai_providers import ai_manager

            # Search for relevant messages
            search_results = self.search(question, chat_id, filters, top_k=10)

            # Check if we have sufficient information
            if not search_results or (search_results and search_results[0]['score'] < 0.1):
                return {
                    "answer": "Недостаточно информации в истории чата для ответа на этот вопрос.",
                    "sources": [],
                    "confidence": "insufficient"
                }

            # Format messages for LLM context
            messages_formatted = []
            for i, result in enumerate(search_results[:10], 1):
                msg_text = f"{i}. [{result['author']} at {result['timestamp']}]"
                if result['message_link']:
                    msg_text += f" ({result['message_link']})"
                msg_text += f"\n{result['text']}\n"
                messages_formatted.append(msg_text)

            messages_context = "\n".join(messages_formatted)

            # Build LLM prompt
            prompt = f"""You are answering a question based ONLY on the provided chat message history.

Question: {question}

Relevant messages (sorted by relevance):
---
{messages_context}
---

Instructions:
1. Answer ONLY based on the provided messages
2. Include references to specific messages using their links: [source](link)
3. If the information is insufficient to answer the question, explicitly say so
4. Use neutral, factual tone
5. Respond in the same language as the question

Format your response as plain text with [source](link) references inline.
If you cannot answer, respond: "Недостаточно информации в истории чата для ответа на этот вопрос."
"""

            # Call LLM using asyncio.to_thread since ai_manager.query is synchronous
            answer = await asyncio.to_thread(ai_manager.query, prompt)

            # Determine confidence based on top score
            top_score = search_results[0]['score'] if search_results else 0.0
            if top_score > 0.5:
                confidence = "high"
            elif top_score > 0.3:
                confidence = "medium"
            else:
                confidence = "low"

            # Format sources (top 5)
            sources = []
            for result in search_results[:5]:
                sources.append({
                    "message_id": result['message_id'],
                    "text": result['text'],
                    "author": result['author'],
                    "timestamp": result['timestamp'],
                    "message_link": result['message_link'],
                    "thread_id": result['thread_id'],
                    "score": result['score']
                })

            return {
                "answer": answer,
                "sources": sources,
                "confidence": confidence
            }

        except Exception as e:
            logger.error("Q&A failed for chat_id=%s: %s", chat_id, e, exc_info=True)
            return {
                "answer": f"Ошибка при обработке вопроса: {str(e)}",
                "sources": [],
                "confidence": "error"
            }
