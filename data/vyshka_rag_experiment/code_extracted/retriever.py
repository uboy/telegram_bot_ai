"""Strategy-aware retriever: uses QueryPlan to choose retrieval strategy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .classifier import QueryPlan, sanitize_query_for_retrieval
from .config import AppConfig
from .index import IndexStore, tokenize

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", flags=re.UNICODE)
LIST_QUESTION_RE = re.compile(r"^\s*(какие|which|what\s+are)\b", flags=re.IGNORECASE)

try:
    from sentence_transformers import CrossEncoder
    _CROSS_ENCODER_AVAILABLE = True
except ImportError:
    _CROSS_ENCODER_AVAILABLE = False


@dataclass
class RetrievedContext:
    chunk_id: int
    text: str
    rerank_score: float


class StrategyRetriever:
    """Retrieves chunks using dense + sparse + structural + head-boost, fused via RRF."""

    def __init__(self, index: IndexStore, config: AppConfig) -> None:
        self.index = index
        self.config = config
        self._reranker = None
        if _CROSS_ENCODER_AVAILABLE:
            from sentence_transformers import CrossEncoder
            self._reranker = CrossEncoder(config.reranker_model)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def dense_search(self, query: str, top_k: int) -> list[int]:
        """Exposed for parallel execution in pipeline."""
        return self.index.dense_search(query, top_k)

    def retrieve(self, query: str, plan: QueryPlan) -> list[RetrievedContext]:
        return self._retrieve_with_dense(query, plan, dense_ids=None)

    def retrieve_with_dense(
        self, query: str, plan: QueryPlan, dense_ids: list[int]
    ) -> list[RetrievedContext]:
        return self._retrieve_with_dense(query, plan, dense_ids=dense_ids)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _retrieve_with_dense(
        self, query: str, plan: QueryPlan, dense_ids: list[int] | None
    ) -> list[RetrievedContext]:
        effective_query = sanitize_query_for_retrieval(query) if plan.is_prompt_injection else query
        n_chunks = len(self.index.chunks)
        pool = self.config.dense_top_k * 5
        candidate_queries = self._candidate_queries(query, plan)
        rank_lists: list[list[int]] = []

        primary_query = candidate_queries[0] if candidate_queries else effective_query
        if dense_ids is None or primary_query != effective_query:
            dense_ids = self.index.dense_search(primary_query, pool)
        if dense_ids:
            rank_lists.append(dense_ids)
        primary_sparse_ids = self.index.sparse_search(primary_query, pool)
        if primary_sparse_ids:
            rank_lists.append(primary_sparse_ids)

        for candidate_query in candidate_queries[1:]:
            dense_variant = self.index.dense_search(candidate_query, pool)
            sparse_variant = self.index.sparse_search(candidate_query, pool)
            if dense_variant:
                rank_lists.append(dense_variant)
            if sparse_variant:
                rank_lists.append(sparse_variant)

        # Structural: exact paragraph match — highest priority
        if plan.paragraph_refs:
            structural_ids = self.index.structural.search(plan.paragraph_refs)
            if structural_ids:
                rank_lists.insert(0, structural_ids)

        # Head-chunk boost (document title / header)
        if plan.boost_early_chunks:
            head_ids = list(range(min(self.config.head_chunk_boost_count, n_chunks)))
            rank_lists.append(head_ids)

        fused_ids = self._rrf_fuse(rank_lists)
        if not fused_ids:
            return []

        rerank_query = primary_query
        contexts = self._rerank(rerank_query, fused_ids, top_k=plan.top_k)
        return self._expand_contexts(rerank_query, plan, contexts)

    @staticmethod
    def _candidate_queries(query: str, plan: QueryPlan) -> list[str]:
        base_query = sanitize_query_for_retrieval(query) if plan.is_prompt_injection else query
        candidates = [base_query] + list(plan.search_queries)
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            cleaned = re.sub(r"\s+", " ", candidate).strip(" ?!.,:;")
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)
        return normalized

    def _rrf_fuse(self, rank_lists: list[list[int]]) -> list[int]:
        fused: Dict[int, float] = {}
        for rank_list in rank_lists:
            for rank, doc_id in enumerate(rank_list, start=1):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (self.config.rrf_k + rank)
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        return [doc_id for doc_id, _ in ranked[: self.config.fused_top_k]]

    def _rerank(self, query: str, doc_ids: list[int], top_k: int) -> list[RetrievedContext]:
        texts = self.index.chunk_texts
        if self._reranker is not None:
            pairs = [[query, texts[doc_id]] for doc_id in doc_ids]
            scores = self._reranker.predict(pairs, batch_size=16, show_progress_bar=False)
            scored = sorted(zip(doc_ids, [float(s) for s in scores]), key=lambda x: x[1], reverse=True)
        else:
            # Fallback: overlap-based scoring
            q_tokens = set(tokenize(query))
            scored_list: list[tuple[int, float]] = []
            for doc_id in doc_ids:
                overlap = len(q_tokens & set(tokenize(texts[doc_id])))
                scored_list.append((doc_id, float(overlap)))
            scored = sorted(scored_list, key=lambda x: x[1], reverse=True)

        return [
            RetrievedContext(chunk_id=doc_id, text=texts[doc_id], rerank_score=score)
            for doc_id, score in scored[:top_k]
        ]

    def _expand_contexts(
        self,
        query: str,
        plan: QueryPlan,
        contexts: list[RetrievedContext],
    ) -> list[RetrievedContext]:
        if not contexts:
            return contexts
        if not self._should_expand_context(query, plan):
            return self._merge_paragraph_contexts(contexts, plan)

        expanded_ids = [context.chunk_id for context in contexts]
        seen = set(expanded_ids)

        for context in contexts[:2]:
            metadata = self.index.chunks[context.chunk_id].metadata
            paragraph_numbers = tuple(getattr(metadata, "paragraph_numbers", ()))
            if not paragraph_numbers and metadata.paragraph_number is not None:
                paragraph_numbers = (metadata.paragraph_number,)
            anchor_paragraph = self._anchor_paragraph_number(paragraph_numbers, plan)
            if anchor_paragraph is None:
                continue
            for chunk_id in self.index.structural.chunk_ids_for_paragraph(anchor_paragraph):
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                expanded_ids.append(chunk_id)

        if len(expanded_ids) == len(contexts):
            return contexts

        expanded_top_k = min(
            len(expanded_ids),
            max(plan.top_k + 4, self.config.context_top_k + 4),
        )
        expanded_contexts = self._rerank(query, expanded_ids, top_k=expanded_top_k)
        return self._merge_paragraph_contexts(expanded_contexts, plan)

    @staticmethod
    def _should_expand_context(query: str, plan: QueryPlan) -> bool:
        if (
            plan.paragraph_refs
            or plan.query_type in {"analytical", "definitional"}
            or plan.expects_exhaustive_list
        ):
            return True
        return bool(LIST_QUESTION_RE.match(query))

    def _merge_paragraph_contexts(
        self,
        contexts: list[RetrievedContext],
        plan: QueryPlan,
    ) -> list[RetrievedContext]:
        if not contexts:
            return contexts
        if not (
            plan.paragraph_refs
            or plan.query_type in {"analytical", "definitional"}
            or plan.expects_exhaustive_list
        ):
            return contexts

        merged: list[RetrievedContext] = []
        seen_paragraphs: set[int] = set()
        seen_chunks: set[int] = set()

        for context in contexts:
            if context.chunk_id in seen_chunks:
                continue
            metadata = self.index.chunks[context.chunk_id].metadata
            paragraph_numbers = tuple(getattr(metadata, "paragraph_numbers", ()))
            if not paragraph_numbers and metadata.paragraph_number is not None:
                paragraph_numbers = (metadata.paragraph_number,)

            paragraph_number = self._anchor_paragraph_number(paragraph_numbers, plan)
            if paragraph_number is not None:
                if paragraph_number in seen_paragraphs:
                    continue
                sibling_ids = self.index.structural.chunk_ids_for_paragraph(paragraph_number)
                if len(sibling_ids) > 1:
                    if plan.query_type == "definitional" and context.chunk_id in sibling_ids:
                        current_idx = sibling_ids.index(context.chunk_id)
                        sibling_ids = sibling_ids[current_idx : current_idx + 3]
                    merged_text = self._merge_chunk_texts(
                        [self.index.chunk_texts[chunk_id] for chunk_id in sibling_ids]
                    )
                    merged.append(
                        RetrievedContext(
                            chunk_id=context.chunk_id,
                            text=merged_text,
                            rerank_score=context.rerank_score,
                        )
                    )
                    seen_paragraphs.add(paragraph_number)
                    seen_chunks.update(sibling_ids)
                    continue
                seen_paragraphs.add(paragraph_number)

            merged.append(context)
            seen_chunks.add(context.chunk_id)

        limit = max(plan.top_k, self.config.context_top_k)
        if plan.expects_exhaustive_list:
            limit = max(limit, self.config.context_top_k + 4)
        return merged[:limit]

    @staticmethod
    def _merge_chunk_texts(texts: list[str]) -> str:
        if not texts:
            return ""
        merged = texts[0].strip()
        for text in texts[1:]:
            merged = StrategyRetriever._append_unique_text(merged, text)
        return merged

    @staticmethod
    def _append_unique_text(current: str, next_text: str) -> str:
        current_text = current.strip()
        appended_text = next_text.strip()
        if not current_text:
            return appended_text
        if not appended_text:
            return current_text

        next_words = appended_text.split()
        search_window = current_text[-800:]
        max_words = min(len(next_words), 40)
        for size in range(max_words, 7, -1):
            prefix = " ".join(next_words[:size])
            if prefix and prefix in search_window:
                suffix = " ".join(next_words[size:]).strip()
                return current_text if not suffix else f"{current_text} {suffix}"

        max_chars = min(len(current_text), len(appended_text), 220)
        for size in range(max_chars, 39, -1):
            if current_text[-size:] == appended_text[:size]:
                suffix = appended_text[size:].strip()
                return current_text if not suffix else f"{current_text} {suffix}"

        return f"{current_text} {appended_text}"

    @staticmethod
    def _anchor_paragraph_number(
        paragraph_numbers: tuple[int, ...],
        plan: QueryPlan,
    ) -> int | None:
        if not paragraph_numbers:
            return None
        for paragraph_ref in plan.paragraph_refs:
            if paragraph_ref in paragraph_numbers:
                return paragraph_ref
        if len(paragraph_numbers) == 1:
            return paragraph_numbers[0]
        return paragraph_numbers[-1]


# ---------------------------------------------------------------------------
# Lexical fallback retriever (no ML dependencies required)
# ---------------------------------------------------------------------------

class LexicalFallbackRetriever:
    """TF-IDF based retriever for restricted environments without ML libs."""

    def __init__(self, chunks: list[str], config: AppConfig) -> None:
        if not chunks:
            raise ValueError("No chunks found in the source document")
        self.chunks = list(chunks)
        self.config = config
        self._vocab: Dict[str, int] = {}
        self._idf: np.ndarray | None = None
        self._matrix: np.ndarray | None = None
        self._norms: np.ndarray | None = None
        self._token_sets: list[set[str]] = []
        self._build_tfidf_index()

    def _build_tfidf_index(self) -> None:
        tokenized_docs = [tokenize(chunk) for chunk in self.chunks]
        self._token_sets = [set(toks) for toks in tokenized_docs]
        df: Dict[str, int] = {}
        for toks in tokenized_docs:
            seen = set(toks)
            for t in seen:
                df[t] = df.get(t, 0) + 1
            for t in toks:
                if t not in self._vocab:
                    self._vocab[t] = len(self._vocab)

        n_docs = len(self.chunks)
        n_vocab = len(self._vocab)
        idf = np.zeros(n_vocab, dtype=np.float32)
        for tok, idx in self._vocab.items():
            idf[idx] = np.log((1.0 + n_docs) / (1.0 + df.get(tok, 0))) + 1.0

        matrix = np.zeros((n_docs, n_vocab), dtype=np.float32)
        for row, toks in enumerate(tokenized_docs):
            if not toks:
                continue
            tf: Dict[int, int] = {}
            for tok in toks:
                col = self._vocab[tok]
                tf[col] = tf.get(col, 0) + 1
            max_tf = max(tf.values())
            for col, cnt in tf.items():
                matrix[row, col] = (cnt / max_tf) * idf[col]

        self._idf = idf
        self._matrix = matrix
        self._norms = np.linalg.norm(matrix, axis=1)

    def _query_vector(self, query: str) -> np.ndarray:
        if self._idf is None:
            raise RuntimeError("TF-IDF index not initialized")
        vec = np.zeros_like(self._idf, dtype=np.float32)
        toks = tokenize(query)
        tf: Dict[int, int] = {}
        for t in toks:
            col = self._vocab.get(t)
            if col is not None:
                tf[col] = tf.get(col, 0) + 1
        if not tf:
            return vec
        max_tf = max(tf.values())
        for col, cnt in tf.items():
            vec[col] = (cnt / max_tf) * self._idf[col]
        return vec

    def retrieve(self, query: str, plan: QueryPlan | None = None) -> list[RetrievedContext]:
        if self._matrix is None or self._norms is None:
            raise RuntimeError("TF-IDF index not initialized")

        top_k = plan.top_k if plan else self.config.context_top_k
        boost_early = plan.boost_early_chunks if plan else False
        candidate_queries = (
            StrategyRetriever._candidate_queries(query, plan)  # type: ignore[arg-type]
            if plan is not None
            else [query]
        )

        score_map = {idx: 0.0 for idx in range(len(self.chunks))}
        for candidate_query in candidate_queries:
            qvec = self._query_vector(candidate_query)
            qnorm = float(np.linalg.norm(qvec))
            if qnorm == 0:
                continue
            numer = self._matrix @ qvec
            denom = self._norms * qnorm
            scores = np.divide(
                numer, denom,
                out=np.zeros_like(numer, dtype=np.float32),
                where=denom != 0,
            )
            for idx in range(len(scores)):
                score_map[idx] = max(score_map[idx], float(scores[idx]))

        if not any(score > 0 for score in score_map.values()):
            return []

        # Structural boost via paragraph refs
        if plan and plan.paragraph_refs:
            q_tokens = set()
            for candidate_query in candidate_queries:
                q_tokens.update(tokenize(candidate_query))
            for idx, chunk in enumerate(self.chunks):
                chunk_tokens = set(tokenize(chunk))
                if q_tokens & chunk_tokens:
                    score_map[idx] = score_map.get(idx, 0.0) + 0.3

        # Head-chunk boost
        if boost_early:
            for rank, chunk_id in enumerate(
                range(min(self.config.head_chunk_boost_count, len(self.chunks))),
                start=1,
            ):
                score_map[chunk_id] = score_map.get(chunk_id, 0.0) + 0.2 / rank

        ranked = sorted(score_map.keys(), key=lambda i: score_map[i], reverse=True)[:top_k]
        return [
            RetrievedContext(
                chunk_id=int(cid),
                text=self.chunks[int(cid)],
                rerank_score=score_map[int(cid)],
            )
            for cid in ranked
            if score_map[int(cid)] > 0
        ]
