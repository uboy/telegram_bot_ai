"""RAG system for chat message search and clustering."""
import logging
from typing import Dict, List, Optional
from datetime import datetime
from collections import Counter
import numpy as np
import faiss

logger = logging.getLogger(__name__)


class ChatAnalyticsRAG:
    """FAISS-based search for chat messages.

    Reuses encoder/reranker from the main RAGSystem but maintains
    separate per-chat FAISS indices.
    """

    def __init__(self):
        self._encoder = None
        self._reranker = None
        self._dimension = None
        self.faiss_indices: Dict[str, object] = {}
        self.message_cache: Dict[str, list] = {}
        self.bm25_indices: Dict[str, object] = {}

    @property
    def encoder(self):
        if self._encoder is None:
            try:
                from shared.rag_system import rag_system
                self._encoder = rag_system.encoder
                self._dimension = rag_system.dimension
                self._reranker = rag_system.reranker
            except Exception as e:
                logger.warning("Could not load encoder from rag_system: %s", e)
        return self._encoder

    def embed_messages(self, texts: List[str]) -> np.ndarray:
        """Batch embed message texts.

        Args:
            texts: List of message texts to embed

        Returns:
            Normalized embeddings as numpy array (shape: [len(texts), dimension])
        """
        if not self.encoder:
            raise RuntimeError("Encoder not initialized. Cannot embed messages.")

        if not texts:
            return np.array([]).reshape(0, self._dimension or 768)

        # Encode texts using sentence-transformers
        embeddings = self.encoder.encode(
            texts,
            batch_size=64,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=False  # We'll normalize with FAISS below
        )

        # Normalize embeddings for cosine similarity (FAISS IndexFlatIP)
        faiss.normalize_L2(embeddings)

        return embeddings

    def build_index(self, chat_id: str, messages: list,
                    embeddings: np.ndarray):
        """Build FAISS index for a chat.

        Args:
            chat_id: Unique chat identifier
            messages: List of message dicts (keys: message_id, text, author_telegram_id,
                      author_display_name, timestamp, message_link, thread_id)
            embeddings: Normalized embeddings array (shape: [len(messages), dimension])
        """
        if embeddings.shape[0] != len(messages):
            raise ValueError(
                f"Embeddings count ({embeddings.shape[0]}) must match "
                f"messages count ({len(messages)})"
            )

        if embeddings.shape[0] == 0:
            logger.warning("No messages to index for chat_id=%s", chat_id)
            self.faiss_indices[chat_id] = None
            self.message_cache[chat_id] = []
            self.bm25_indices[chat_id] = {}
            return

        # Create FAISS index (IndexFlatIP for inner product, works with normalized vectors)
        dimension = embeddings.shape[1]
        index = faiss.IndexFlatIP(dimension)
        index.add(embeddings)

        # Store index and messages
        self.faiss_indices[chat_id] = index
        self.message_cache[chat_id] = messages

        # Build BM25 index
        self.bm25_indices[chat_id] = self._build_bm25(messages)

        logger.info(
            "Built FAISS index for chat_id=%s: %d messages, dimension=%d",
            chat_id, len(messages), dimension
        )

    def search(self, query: str, chat_id: str, top_k: int = 10,
               filters: Optional[dict] = None) -> list:
        """Hybrid search: semantic + BM25 + RRF fusion.

        Args:
            query: Search query string
            chat_id: Chat identifier
            top_k: Number of top results to return
            filters: Optional filters dict with keys:
                - thread_id: Filter by thread ID
                - author_telegram_id: Filter by author Telegram ID
                - period_start: Filter by start datetime (inclusive)
                - period_end: Filter by end datetime (inclusive)

        Returns:
            List of dicts with keys: message_id, text, author, timestamp,
            message_link, thread_id, score
        """
        if chat_id not in self.faiss_indices:
            logger.warning("No index found for chat_id=%s", chat_id)
            return []

        if not self.faiss_indices[chat_id]:
            logger.warning("Empty index for chat_id=%s", chat_id)
            return []

        # Perform hybrid search
        semantic_results = self._semantic_search(query, chat_id, top_k * 2)
        bm25_results = self._bm25_search(query, chat_id, top_k * 2)

        # Fuse results using RRF
        fused_results = self._rrf_fuse([semantic_results, bm25_results], k=60)

        # Apply filters
        if filters:
            fused_results = self._apply_filters(fused_results, filters)

        # Return top_k
        return fused_results[:top_k]

    def _semantic_search(self, query: str, chat_id: str, top_k: int) -> list:
        """Perform semantic search using FAISS.

        Args:
            query: Query string
            chat_id: Chat identifier
            top_k: Number of results to retrieve

        Returns:
            List of message dicts with score
        """
        if not self.encoder:
            logger.warning("Encoder not available for semantic search")
            return []

        index = self.faiss_indices.get(chat_id)
        messages = self.message_cache.get(chat_id, [])

        if not index or not messages:
            return []

        # Embed query
        query_embedding = self.encoder.encode(
            [query],
            batch_size=1,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=False
        )
        faiss.normalize_L2(query_embedding)

        # Search FAISS index
        k = min(top_k, len(messages))
        distances, indices = index.search(query_embedding, k)

        # Build results
        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < 0 or idx >= len(messages):
                continue

            msg = messages[idx].copy()
            msg['score'] = float(distance)
            msg['author'] = msg.get('author_display_name', 'Unknown')
            results.append(msg)

        return results

    def _bm25_search(self, query: str, chat_id: str, top_k: int) -> list:
        """Perform BM25-like keyword search.

        Args:
            query: Query string
            chat_id: Chat identifier
            top_k: Number of results to retrieve

        Returns:
            List of message dicts with score
        """
        bm25_index = self.bm25_indices.get(chat_id, {})
        messages = self.message_cache.get(chat_id, [])

        if not bm25_index or not messages:
            return []

        # Tokenize query (simple lowercase split)
        query_tokens = query.lower().split()
        if not query_tokens:
            return []

        # Score each message
        scores = []
        for i, msg in enumerate(messages):
            doc_tokens = bm25_index.get(i, [])
            if not doc_tokens:
                scores.append(0.0)
                continue

            # Simple BM25-like scoring: count matching terms with TF weighting
            doc_token_counts = Counter(doc_tokens)
            score = 0.0
            for token in query_tokens:
                if token in doc_token_counts:
                    # Simple TF component (log-scaled)
                    tf = doc_token_counts[token]
                    score += np.log(1 + tf)

            scores.append(score)

        # Get top_k by score
        scored_indices = sorted(
            enumerate(scores),
            key=lambda x: x[1],
            reverse=True
        )[:top_k]

        # Build results
        results = []
        for idx, score in scored_indices:
            if score <= 0:
                continue

            msg = messages[idx].copy()
            msg['score'] = score
            msg['author'] = msg.get('author_display_name', 'Unknown')
            results.append(msg)

        return results

    def _rrf_fuse(self, result_lists: List[list], k: int = 60) -> list:
        """Reciprocal Rank Fusion of multiple result lists.

        Args:
            result_lists: List of result lists (each result is a dict with message_id)
            k: RRF constant (default 60)

        Returns:
            Fused list of results sorted by RRF score
        """
        rrf_scores = {}

        for result_list in result_lists:
            for rank, result in enumerate(result_list, start=1):
                msg_id = result.get('message_id')
                if msg_id is None:
                    continue

                # RRF formula: 1 / (k + rank)
                score = 1.0 / (k + rank)

                if msg_id not in rrf_scores:
                    rrf_scores[msg_id] = {
                        'result': result,
                        'score': 0.0
                    }
                rrf_scores[msg_id]['score'] += score

        # Sort by RRF score
        fused = sorted(
            rrf_scores.values(),
            key=lambda x: x['score'],
            reverse=True
        )

        # Extract results with RRF scores
        final_results = []
        for item in fused:
            result = item['result'].copy()
            result['score'] = item['score']
            final_results.append(result)

        return final_results

    def _build_bm25(self, messages: list) -> Dict[int, List[str]]:
        """Build simple BM25 token index.

        Args:
            messages: List of message dicts

        Returns:
            Dict mapping message index to list of tokens
        """
        bm25_index = {}
        for i, msg in enumerate(messages):
            text = msg.get('text', '')
            # Simple tokenization: lowercase and split
            tokens = text.lower().split()
            bm25_index[i] = tokens

        return bm25_index

    def _apply_filters(self, results: list, filters: dict) -> list:
        """Apply filters to search results.

        Args:
            results: List of message dicts
            filters: Dict with optional keys: thread_id, author_telegram_id,
                     period_start, period_end

        Returns:
            Filtered list of results
        """
        filtered = []

        thread_id = filters.get('thread_id')
        author_id = filters.get('author_telegram_id')
        period_start = filters.get('period_start')
        period_end = filters.get('period_end')

        for result in results:
            # Filter by thread_id
            if thread_id is not None:
                if result.get('thread_id') != thread_id:
                    continue

            # Filter by author
            if author_id is not None:
                if result.get('author_telegram_id') != author_id:
                    continue

            # Filter by period_start
            if period_start is not None:
                timestamp = result.get('timestamp')
                if timestamp:
                    # Handle both datetime and string timestamps
                    if isinstance(timestamp, str):
                        try:
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        except Exception:
                            pass
                    if isinstance(timestamp, datetime):
                        if timestamp < period_start:
                            continue

            # Filter by period_end
            if period_end is not None:
                timestamp = result.get('timestamp')
                if timestamp:
                    # Handle both datetime and string timestamps
                    if isinstance(timestamp, str):
                        try:
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        except Exception:
                            pass
                    if isinstance(timestamp, datetime):
                        if timestamp > period_end:
                            continue

            filtered.append(result)

        return filtered


# Singleton instance
chat_analytics_rag = ChatAnalyticsRAG()
