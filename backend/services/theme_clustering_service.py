"""Semantic clustering of chat messages into themes."""
import logging
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from collections import Counter
import numpy as np

from shared.ai_providers import ai_manager
from shared.config import ANALYTICS_CLUSTER_METHOD, ANALYTICS_CLUSTER_MIN_SIZE

logger = logging.getLogger(__name__)


class ThemeClusteringService:
    """Clusters messages by semantic similarity and extracts themes via LLM."""

    def preprocess_messages(self, messages: list) -> list:
        """Merge short messages into blocks for better clustering.

        Groups consecutive messages from the same author in the same thread
        within 5 minutes into blocks. Blocks with text < 20 chars are discarded.

        Args:
            messages: List of dicts with keys:
                - message_id (int)
                - text (str)
                - author_telegram_id (str)
                - author_display_name (str)
                - timestamp (datetime or str)
                - thread_id (int or None)
                - message_link (str)

        Returns:
            List of block dicts with keys:
                - text (str): concatenated text
                - message_ids (list): list of message IDs in this block
                - author_display_name (str)
                - thread_id (int or None)
                - message_link (str): from first message
                - timestamp (datetime): from first message
        """
        MIN_BLOCK_LENGTH = 20
        MAX_TIME_GAP_SECONDS = 5 * 60  # 5 minutes

        if not messages:
            return []

        # Sort by thread_id (with None first), then by timestamp
        sorted_messages = sorted(
            messages,
            key=lambda m: (
                m.get('thread_id') or 0,
                self._parse_timestamp(m.get('timestamp'))
            )
        )

        blocks = []
        current_block = None

        for msg in sorted_messages:
            text = (msg.get('text') or '').strip()
            if not text:
                continue

            timestamp = self._parse_timestamp(msg.get('timestamp'))
            author_id = msg.get('author_telegram_id')
            thread_id = msg.get('thread_id')

            should_merge = False

            if current_block:
                # Check if we should merge with current block
                time_gap = (timestamp - current_block['timestamp']).total_seconds()
                same_author = current_block['author_telegram_id'] == author_id
                same_thread = current_block['thread_id'] == thread_id

                should_merge = (
                    same_author and
                    same_thread and
                    time_gap <= MAX_TIME_GAP_SECONDS
                )

            if should_merge:
                # Merge with current block
                current_block['text'] += '\n' + text
                current_block['message_ids'].append(msg.get('message_id'))
            else:
                # Save previous block if it meets length requirement
                if current_block and len(current_block['text']) >= MIN_BLOCK_LENGTH:
                    blocks.append(current_block)

                # Start new block
                current_block = {
                    'text': text,
                    'message_ids': [msg.get('message_id')],
                    'author_telegram_id': author_id,
                    'author_display_name': msg.get('author_display_name', 'Unknown'),
                    'thread_id': thread_id,
                    'message_link': msg.get('message_link'),
                    'timestamp': timestamp,
                }

        # Don't forget the last block
        if current_block and len(current_block['text']) >= MIN_BLOCK_LENGTH:
            blocks.append(current_block)

        logger.info(
            "Preprocessed %d messages into %d blocks",
            len(messages),
            len(blocks)
        )

        return blocks

    def cluster_messages(self, embeddings: np.ndarray,
                         min_cluster_size: int = 5,
                         method: str = "hdbscan") -> np.ndarray:
        """Cluster embeddings using HDBSCAN or fallback.

        Args:
            embeddings: (N, D) array of L2-normalized embeddings
            min_cluster_size: minimum number of messages in a cluster
            method: "hdbscan" or "agglomerative"

        Returns:
            labels: (N,) array of cluster labels, -1 for noise/outliers
        """
        if embeddings.shape[0] < min_cluster_size:
            logger.warning(
                "Too few embeddings (%d) for clustering (min: %d). Returning single cluster.",
                embeddings.shape[0],
                min_cluster_size
            )
            return np.zeros(embeddings.shape[0], dtype=int)

        try:
            if method == "hdbscan":
                labels = self._cluster_hdbscan(embeddings, min_cluster_size)
            else:
                labels = self._cluster_agglomerative(embeddings)

            unique_labels = set(labels)
            n_clusters = len(unique_labels - {-1})  # Exclude noise label
            n_noise = np.sum(labels == -1)

            logger.info(
                "Clustering complete: %d clusters, %d noise points, method=%s",
                n_clusters,
                n_noise,
                method
            )

            return labels

        except Exception as e:
            logger.error(
                "Clustering failed with method=%s: %s. Trying fallback.",
                method,
                e,
                exc_info=True
            )

            # Fallback to agglomerative if HDBSCAN fails
            if method == "hdbscan":
                try:
                    return self._cluster_agglomerative(embeddings)
                except Exception as e2:
                    logger.error(
                        "Fallback clustering also failed: %s. Returning single cluster.",
                        e2
                    )
                    return np.zeros(embeddings.shape[0], dtype=int)
            else:
                logger.error("No fallback available. Returning single cluster.")
                return np.zeros(embeddings.shape[0], dtype=int)

    def _cluster_hdbscan(self, embeddings: np.ndarray, min_cluster_size: int) -> np.ndarray:
        """Cluster using HDBSCAN."""
        try:
            import hdbscan
        except ImportError:
            logger.error("hdbscan not installed. Falling back to agglomerative.")
            return self._cluster_agglomerative(embeddings)

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=2,
            metric='euclidean',
            cluster_selection_method='eom',
        )

        labels = clusterer.fit_predict(embeddings)
        return labels

    def _cluster_agglomerative(self, embeddings: np.ndarray) -> np.ndarray:
        """Cluster using Agglomerative Clustering."""
        from sklearn.cluster import AgglomerativeClustering

        clusterer = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=1.2,
            metric='euclidean',
            linkage='ward',
        )

        labels = clusterer.fit_predict(embeddings)
        return labels

    async def extract_theme(self, cluster_messages: list,
                            cluster_id: int) -> dict:
        """Extract theme from a cluster via LLM.

        Args:
            cluster_messages: List of message block dicts
            cluster_id: Integer ID of the cluster

        Returns:
            Dict with keys:
                - emoji (str)
                - title (str)
                - summary (str)
                - key_decisions (list)
                - unresolved_questions (list)
                - main_participants (list)
                - message_count (int)
                - key_message_links (list)
                - sort_order (int)
        """
        if not cluster_messages:
            return self._fallback_theme(cluster_id)

        # Select up to 10 representative messages (longest text)
        representative = sorted(
            cluster_messages,
            key=lambda m: len(m.get('text', '')),
            reverse=True
        )[:10]

        # Format messages for LLM
        formatted_messages = self._format_messages_for_prompt(representative)

        # Build LLM prompt
        prompt = f"""You are analyzing a cluster of chat messages from a Telegram group discussion.
Your task is to identify the main theme of this cluster.

Messages (from the cluster):
---
{formatted_messages}
---

Instructions:
1. Identify the main topic being discussed
2. Write a concise title (max 10 words)
3. Write a summary (2-6 sentences) describing the key points
4. Pick a single emoji that represents this theme
5. List any decisions that were made
6. List any unresolved questions

IMPORTANT:
- Respond in the SAME LANGUAGE as the messages
- Be factual -- do NOT add information not present in the messages
- Use neutral tone
- Return ONLY valid JSON

JSON format:
{{
  "emoji": "<emoji>",
  "title": "<title>",
  "summary": "<summary>",
  "key_decisions": ["<decision>", ...],
  "unresolved_questions": ["<question>", ...]
}}"""

        try:
            # Query LLM
            response = await self._query_llm(prompt)
            theme_data = self._parse_llm_response(response)
        except Exception as e:
            logger.error("LLM theme extraction failed: %s", e, exc_info=True)
            theme_data = self._fallback_theme_data()

        # Determine main participants
        authors = [msg.get('author_display_name', 'Unknown')
                   for msg in cluster_messages]
        author_counts = Counter(authors)
        main_participants = [
            author for author, _ in author_counts.most_common(5)
        ]

        # Collect key message links (first 5 unique)
        all_links = [msg.get('message_link') for msg in cluster_messages
                     if msg.get('message_link')]
        key_message_links = []
        seen = set()
        for link in all_links:
            if link and link not in seen:
                key_message_links.append(link)
                seen.add(link)
                if len(key_message_links) >= 5:
                    break

        # Build final theme dict
        theme = {
            'emoji': theme_data.get('emoji', '💬'),
            'title': theme_data.get('title', f'Theme {cluster_id}'),
            'summary': theme_data.get('summary', 'Discussion cluster'),
            'key_decisions': theme_data.get('key_decisions', []),
            'unresolved_questions': theme_data.get('unresolved_questions', []),
            'main_participants': main_participants,
            'message_count': len(cluster_messages),
            'key_message_links': key_message_links,
            'sort_order': cluster_id,
        }

        return theme

    def _parse_timestamp(self, timestamp) -> datetime:
        """Parse timestamp from datetime or string."""
        if isinstance(timestamp, datetime):
            return timestamp
        elif isinstance(timestamp, str):
            try:
                # Try ISO format
                return datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except Exception:
                # Fallback to now
                logger.warning("Failed to parse timestamp: %s", timestamp)
                return datetime.utcnow()
        else:
            return datetime.utcnow()

    def _format_messages_for_prompt(self, messages: list) -> str:
        """Format messages for LLM prompt."""
        formatted = []
        for i, msg in enumerate(messages, 1):
            author = msg.get('author_display_name', 'Unknown')
            text = msg.get('text', '')
            timestamp = self._parse_timestamp(msg.get('timestamp'))
            timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M')

            formatted.append(f"{i}. [{timestamp_str}] {author}:\n{text}\n")

        return '\n'.join(formatted)

    async def _query_llm(self, prompt: str) -> str:
        """Query LLM asynchronously."""
        import asyncio

        # ai_manager.query is synchronous, so run in thread pool
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, ai_manager.query, prompt)

        return response

    def _parse_llm_response(self, response: str) -> dict:
        """Parse JSON response from LLM."""
        # Try to extract JSON from response
        response = response.strip()

        # Remove markdown code blocks if present
        if response.startswith('```'):
            lines = response.split('\n')
            # Remove first line (```json or ```)
            lines = lines[1:]
            # Remove last line (```)
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            response = '\n'.join(lines)

        try:
            data = json.loads(response)
            return data
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM JSON response: %s", e)
            logger.debug("Response was: %s", response[:500])
            return self._fallback_theme_data()

    def _fallback_theme_data(self) -> dict:
        """Return fallback theme data when LLM fails."""
        return {
            'emoji': '💬',
            'title': 'Discussion cluster',
            'summary': 'A group of related messages.',
            'key_decisions': [],
            'unresolved_questions': [],
        }

    def _fallback_theme(self, cluster_id: int) -> dict:
        """Return fallback theme when no messages."""
        return {
            'emoji': '💬',
            'title': f'Theme {cluster_id}',
            'summary': 'Empty cluster',
            'key_decisions': [],
            'unresolved_questions': [],
            'main_participants': [],
            'message_count': 0,
            'key_message_links': [],
            'sort_order': cluster_id,
        }
