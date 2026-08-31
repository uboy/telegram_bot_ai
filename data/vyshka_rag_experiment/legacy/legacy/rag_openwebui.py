#!/usr/bin/env python3
"""High-quality RAG pipeline with Ollama-backed generation."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Protocol, Sequence, Tuple

import fitz
import numpy as np
import requests
from dotenv import load_dotenv

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover
    BM25Okapi = None  # type: ignore[assignment]

try:
    from sentence_transformers import CrossEncoder, SentenceTransformer
except ImportError:  # pragma: no cover
    CrossEncoder = None  # type: ignore[assignment]
    SentenceTransformer = None  # type: ignore[assignment]

try:
    import faiss
except ImportError:  # pragma: no cover
    faiss = None  # type: ignore[assignment]


WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", flags=re.UNICODE)
SPACE_RE = re.compile(r"\s+")
TITLE_CHANGE_RE = re.compile(r"с изменениями\s+\d{4}\s*г", flags=re.IGNORECASE)
NO_DATA_MARKERS = (
    "нет данных",
    "недостаточно данных",
    "не представляется возможным определить",
    "невозможно определить",
    "в контексте отсутствует",
    "информация отсутствует",
)
FACTOID_PREFIXES = (
    "какой",
    "какая",
    "какие",
    "каково",
    "кто",
    "когда",
    "где",
    "сколько",
    "чему",
    "каким",
    "каких",
    "на какой",
)
FACTOID_KEYWORDS = (
    "пункт",
    "подпункт",
    "заголов",
    "изменен",
    "изменени",
    "целевой показатель",
    "номер",
    "дата",
)


def normalize_text(text: str) -> str:
    return SPACE_RE.sub(" ", text).strip()


def tokenize(text: str) -> List[str]:
    return [tok.lower() for tok in WORD_RE.findall(text)]


def is_factoid_query(query: str) -> bool:
    q = normalize_text(query).lower()
    if not q:
        return False
    if q.startswith(FACTOID_PREFIXES):
        return True
    return any(keyword in q for keyword in FACTOID_KEYWORDS)


def extract_header_chunk(pdf_path: Path, max_chars: int = 700) -> str:
    doc = fitz.open(pdf_path)
    first_page = doc[0].get_text("text") if len(doc) > 0 else ""
    doc.close()

    text = normalize_text(first_page)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 80:
        truncated = truncated[:last_space]
    return truncated.strip()


def extract_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    pages = [page.get_text("text") for page in doc]
    doc.close()
    return "\n".join(pages)


def split_chunks(text: str, chunk_size: int, overlap: int) -> List[str]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    text = normalize_text(text)
    if not text:
        return []

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            aligned = end
            while aligned > start and not text[aligned - 1].isspace():
                aligned -= 1
            if aligned > start + 50:
                end = aligned
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(0, end - overlap)
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def read_questions(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    if suffix == ".txt":
        lines = path.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip()]

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV questions file has no header")
            question_column = "question" if "question" in reader.fieldnames else reader.fieldnames[0]
            questions: List[str] = []
            for row in reader:
                q = (row.get(question_column) or "").strip()
                if q:
                    questions.append(q)
            return questions

    raise ValueError("Questions file must be .txt or .csv")


def write_answers_only(path: Path, answers: Sequence[str], with_header: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if with_header:
            writer.writerow(["answers"])
        for answer in answers:
            writer.writerow([answer])


def write_debug_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "question",
                "answer",
                "sources",
                "rerank_scores",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


@dataclass
class AppConfig:
    base_url: str
    api_token: str | None
    model_id: str | None
    embedding_model: str
    reranker_model: str
    chunk_size: int
    overlap: int
    dense_top_k: int
    sparse_top_k: int
    fused_top_k: int
    context_top_k: int
    rrf_k: int
    temperature: float
    max_tokens: int
    seed: int | None
    timeout_sec: int
    use_env_proxy: bool
    disable_thinking: bool
    factoid_context_top_k: int
    head_chunk_boost_count: int
    min_rerank_score: float

    @staticmethod
    def from_env() -> "AppConfig":
        load_dotenv()

        base_url = os.getenv("OLLAMA_BASE_URL", "").strip()
        token = os.getenv("OLLAMA_API_TOKEN", "").strip() or None
        model_id = os.getenv("OLLAMA_MODEL", "").strip() or None
        embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip()
        reranker_model = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3").strip()

        if not base_url:
            raise ValueError("OLLAMA_BASE_URL is required in .env")

        seed_raw = os.getenv("OLLAMA_SEED", "").strip()
        seed = int(seed_raw) if seed_raw else None

        return AppConfig(
            base_url=base_url.rstrip("/"),
            api_token=token,
            model_id=model_id,
            embedding_model=embedding_model,
            reranker_model=reranker_model,
            chunk_size=int(os.getenv("CHUNK_SIZE", "1100")),
            overlap=int(os.getenv("CHUNK_OVERLAP", "220")),
            dense_top_k=int(os.getenv("DENSE_TOP_K", "40")),
            sparse_top_k=int(os.getenv("SPARSE_TOP_K", "40")),
            fused_top_k=int(os.getenv("FUSED_TOP_K", "30")),
            context_top_k=int(os.getenv("CONTEXT_TOP_K", "5")),
            rrf_k=int(os.getenv("RRF_K", "60")),
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.0").strip()),
            max_tokens=int(os.getenv("OLLAMA_NUM_PREDICT", "420").strip()),
            seed=seed,
            timeout_sec=int(os.getenv("OLLAMA_TIMEOUT_SEC", "180").strip()),
            use_env_proxy=os.getenv("OLLAMA_USE_ENV_PROXY", "0").strip().lower() in ("1", "true", "yes", "on"),
            disable_thinking=os.getenv("OLLAMA_DISABLE_THINKING", "1").strip().lower() in ("1", "true", "yes", "on"),
            factoid_context_top_k=int(os.getenv("FACTOID_CONTEXT_TOP_K", "3").strip()),
            head_chunk_boost_count=int(os.getenv("HEAD_CHUNK_BOOST_COUNT", "3").strip()),
            min_rerank_score=float(os.getenv("MIN_RERANK_SCORE", "0.02").strip()),
        )


class OllamaClient:
    def __init__(self, config: AppConfig) -> None:
        self.base_url = config.base_url
        self.token = config.api_token
        self.timeout_sec = config.timeout_sec
        self.model_id = config.model_id
        self.disable_thinking = config.disable_thinking
        self._session = requests.Session()
        self._session.trust_env = config.use_env_proxy

    @property
    def headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        retries = 3
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                resp = self._session.request(
                    method=method,
                    url=self._url(path),
                    timeout=self.timeout_sec,
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(1.5 * attempt)
                    continue
                raise RuntimeError(f"Ollama request failed: {exc}") from exc

            if resp.status_code in (502, 503, 504) and attempt < retries:
                time.sleep(1.5 * attempt)
                continue
            if resp.status_code >= 400:
                body = (resp.text or "").strip()
                if len(body) > 800:
                    body = body[:800] + "..."
                raise RuntimeError(
                    f"Ollama error {resp.status_code} on {path}. "
                    f"Response body: {body if body else '<empty>'}"
                )
            return resp

        if last_exc is not None:
            raise RuntimeError(f"Ollama request failed: {last_exc}") from last_exc
        raise RuntimeError("Ollama request failed for unknown reason")

    def list_models(self) -> List[str]:
        response = self._request(
            "GET",
            "/api/tags",
            headers=self.headers,
        )
        payload = response.json()

        model_ids: List[str] = []
        candidates = payload.get("models", []) if isinstance(payload, dict) else []

        for item in candidates:
            if not isinstance(item, dict):
                continue
            model_id = item.get("name") or item.get("model")
            if isinstance(model_id, str) and model_id.strip():
                model_ids.append(model_id.strip())
        return model_ids

    def resolve_model(self) -> str:
        if self.model_id:
            return self.model_id
        models = self.list_models()
        if not models:
            raise RuntimeError(
                "No models returned by Ollama. Set OLLAMA_MODEL explicitly in .env"
            )
        self.model_id = models[0]
        return self.model_id

    def generate(
        self,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        seed: int | None,
    ) -> str:
        options: Dict[str, object] = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if seed is not None:
            options["seed"] = seed

        payload: Dict[str, object] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": options,
        }
        if self.disable_thinking:
            payload["think"] = False

        response = self._request(
            "POST",
            "/api/chat",
            headers=self.headers,
            json=payload,
        )
        data = response.json()
        message = data.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if isinstance(text, str):
            stripped = text.strip()
            if stripped:
                return stripped

        # Fallback for models that still emit only reasoning text in /api/chat.
        fallback_payload: Dict[str, object] = {
            "model": model_id,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
            "options": options,
        }
        if self.disable_thinking:
            fallback_payload["think"] = False

        fallback_response = self._request(
            "POST",
            "/api/generate",
            headers=self.headers,
            json=fallback_payload,
        )
        fallback_data = fallback_response.json()
        fallback_text = fallback_data.get("response")
        if isinstance(fallback_text, str):
            stripped = fallback_text.strip()
            if stripped:
                return stripped

        raise RuntimeError(
            "Unexpected Ollama response. "
            f"/api/chat={json.dumps(data, ensure_ascii=False)}; "
            f"/api/generate={json.dumps(fallback_data, ensure_ascii=False)}"
        )


@dataclass
class RetrievedContext:
    chunk_id: int
    text: str
    rerank_score: float


class RetrieverProtocol(Protocol):
    def retrieve(self, query: str) -> List[RetrievedContext]:
        ...


class HybridRetriever:
    def __init__(self, chunks: Sequence[str], config: AppConfig) -> None:
        if not chunks:
            raise ValueError("No chunks found in the source document")
        self.chunks = list(chunks)
        self.config = config

        if SentenceTransformer is None or CrossEncoder is None or BM25Okapi is None or faiss is None:
            raise RuntimeError(
                "HybridRetriever dependencies are missing. Install: "
                "sentence-transformers, rank-bm25, faiss-cpu"
            )

        self.embedder = SentenceTransformer(config.embedding_model)
        self.reranker = CrossEncoder(config.reranker_model)

        self._dense_embeddings = self.embedder.encode(
            self.chunks,
            batch_size=32,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)
        self._index = faiss.IndexFlatIP(self._dense_embeddings.shape[1])
        self._index.add(self._dense_embeddings)

        self._tokenized_chunks = [tokenize(chunk) for chunk in self.chunks]
        self._token_sets = [set(tokens) for tokens in self._tokenized_chunks]
        self._bm25 = BM25Okapi(self._tokenized_chunks)

    @staticmethod
    def _format_query(query: str) -> str:
        return f"Represent this sentence for searching relevant passages: {query}"

    def _dense_search(self, query: str, top_k: int) -> List[int]:
        qvec = self.embedder.encode(
            [self._format_query(query)],
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)
        scores, ids = self._index.search(qvec, top_k)
        result: List[int] = []
        for doc_id, score in zip(ids[0], scores[0]):
            if doc_id < 0:
                continue
            if float(score) <= 0:
                continue
            result.append(int(doc_id))
        return result

    def _sparse_search(self, query: str, top_k: int) -> List[int]:
        q_tokens = tokenize(query)
        scores = self._bm25.get_scores(q_tokens)
        ids = np.argsort(-scores)[:top_k]
        return [int(i) for i in ids]

    def _exact_overlap_search(self, query: str, top_k: int) -> List[int]:
        q_tokens = set(tokenize(query))
        if not q_tokens:
            return []

        query_lc = query.lower()
        scored: List[Tuple[int, float]] = []
        for doc_id, token_set in enumerate(self._token_sets):
            overlap = len(q_tokens & token_set)
            if overlap == 0:
                continue
            score = float(overlap)
            if "заголов" in query_lc and "измен" in query_lc and TITLE_CHANGE_RE.search(self.chunks[doc_id]):
                score += 20.0
            scored.append((doc_id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [doc_id for doc_id, _ in scored[:top_k]]

    def _rrf_fuse(self, rank_lists: Sequence[List[int]]) -> List[int]:
        fused: Dict[int, float] = {}
        for rank_list in rank_lists:
            for rank, doc_id in enumerate(rank_list, start=1):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (self.config.rrf_k + rank)
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        return [doc_id for doc_id, _ in ranked[: self.config.fused_top_k]]

    def retrieve(self, query: str) -> List[RetrievedContext]:
        factoid_mode = is_factoid_query(query)
        dense_ids = self._dense_search(query, self.config.dense_top_k)
        sparse_top_k = self.config.sparse_top_k * 2 if factoid_mode else self.config.sparse_top_k
        sparse_ids = self._sparse_search(query, sparse_top_k)

        rank_lists: List[List[int]] = [dense_ids, sparse_ids]
        if factoid_mode:
            exact_ids = self._exact_overlap_search(query, sparse_top_k)
            head_ids = list(range(min(self.config.head_chunk_boost_count, len(self.chunks))))
            rank_lists.extend([exact_ids, head_ids])

        fused_ids = self._rrf_fuse(rank_lists)
        if not fused_ids:
            return []

        pairs = [[query, self.chunks[doc_id]] for doc_id in fused_ids]
        scores = self.reranker.predict(pairs, batch_size=16, show_progress_bar=False)
        scored = list(zip(fused_ids, [float(score) for score in scores]))
        scored.sort(key=lambda x: x[1], reverse=True)

        contexts: List[RetrievedContext] = []
        limit = self.config.factoid_context_top_k if factoid_mode else self.config.context_top_k
        for doc_id, score in scored[:limit]:
            contexts.append(
                RetrievedContext(
                    chunk_id=int(doc_id),
                    text=self.chunks[int(doc_id)],
                    rerank_score=float(score),
                )
            )
        return contexts


class LexicalFallbackRetriever:
    """Fallback retriever for restricted environments without ML dependencies."""

    def __init__(self, chunks: Sequence[str], config: AppConfig) -> None:
        if not chunks:
            raise ValueError("No chunks found in the source document")
        self.chunks = list(chunks)
        self.config = config

        self._vocab: Dict[str, int] = {}
        self._idf: np.ndarray | None = None
        self._matrix: np.ndarray | None = None
        self._norms: np.ndarray | None = None
        self._build_tfidf_index()

    def _build_tfidf_index(self) -> None:
        tokenized_docs = [tokenize(chunk) for chunk in self.chunks]
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

    def _exact_overlap_ids(self, query: str, top_k: int) -> List[int]:
        q_tokens = set(tokenize(query))
        if not q_tokens:
            return []

        query_lc = query.lower()
        scored: List[Tuple[int, float]] = []
        for idx, chunk in enumerate(self.chunks):
            token_set = set(tokenize(chunk))
            overlap = len(q_tokens & token_set)
            if overlap == 0:
                continue
            score = float(overlap)
            if "заголов" in query_lc and "измен" in query_lc and TITLE_CHANGE_RE.search(chunk):
                score += 20.0
            scored.append((idx, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in scored[:top_k]]

    def retrieve(self, query: str) -> List[RetrievedContext]:
        if self._matrix is None or self._norms is None:
            raise RuntimeError("TF-IDF index not initialized")
        factoid_mode = is_factoid_query(query)
        qvec = self._query_vector(query)
        qnorm = np.linalg.norm(qvec)
        if qnorm == 0:
            return []
        numer = self._matrix @ qvec
        denom = self._norms * qnorm
        scores = np.divide(
            numer,
            denom,
            out=np.zeros_like(numer, dtype=np.float32),
            where=denom != 0,
        )

        if factoid_mode:
            score_map = {idx: float(scores[idx]) for idx in range(len(scores))}
            exact_ids = self._exact_overlap_ids(query, top_k=self.config.sparse_top_k)
            for rank, chunk_id in enumerate(exact_ids, start=1):
                score_map[chunk_id] = score_map.get(chunk_id, 0.0) + 0.35 / rank
            for rank, chunk_id in enumerate(
                range(min(self.config.head_chunk_boost_count, len(self.chunks))),
                start=1,
            ):
                score_map[chunk_id] = score_map.get(chunk_id, 0.0) + 0.2 / rank
            ranked = sorted(score_map.keys(), key=lambda i: score_map[i], reverse=True)[
                : self.config.factoid_context_top_k
            ]
        else:
            ranked = np.argsort(-scores)[: self.config.context_top_k]

        contexts: List[RetrievedContext] = []
        for chunk_id in ranked:
            score = float(scores[int(chunk_id)])
            if score <= 0 and not factoid_mode:
                continue
            contexts.append(
                RetrievedContext(
                    chunk_id=int(chunk_id),
                    text=self.chunks[int(chunk_id)],
                    rerank_score=score,
                )
            )
        return contexts


class RAGPipeline:
    SYSTEM_PROMPT = (
        "Ты помощник по документу. "
        "Отвечай только на основе предоставленного контекста. "
        "Если ответа в контексте нет, ответь: 'Нет данных в предоставленном документе'. "
        "Не добавляй внешние факты."
    )

    def __init__(self, config: AppConfig, retriever: RetrieverProtocol, client: OllamaClient) -> None:
        self.config = config
        self.retriever = retriever
        self.client = client
        self.model_id = client.resolve_model()

    @staticmethod
    def _looks_like_no_data(answer: str) -> bool:
        answer_lc = answer.lower()
        return any(marker in answer_lc for marker in NO_DATA_MARKERS)

    @staticmethod
    def _question_tokens(question: str) -> set[str]:
        return {tok for tok in tokenize(question) if len(tok) >= 4}

    def _max_keyword_overlap(self, question: str, contexts: Sequence[RetrievedContext]) -> int:
        q_tokens = self._question_tokens(question)
        if not q_tokens:
            return 0
        best = 0
        for ctx in contexts:
            overlap = len(q_tokens & set(tokenize(ctx.text)))
            if overlap > best:
                best = overlap
        return best

    def _has_direct_evidence(self, question: str, contexts: Sequence[RetrievedContext]) -> bool:
        overlap = self._max_keyword_overlap(question, contexts)
        if overlap >= 2:
            return True

        q_lc = question.lower()
        joined_context = " ".join(ctx.text.lower() for ctx in contexts)
        if "заголов" in q_lc and "измен" in q_lc and TITLE_CHANGE_RE.search(joined_context):
            return True
        return False

    def _low_confidence_context(self, question: str, contexts: Sequence[RetrievedContext]) -> bool:
        max_score = max((ctx.rerank_score for ctx in contexts), default=0.0)
        overlap = self._max_keyword_overlap(question, contexts)
        return max_score < self.config.min_rerank_score and overlap < 2

    def answer(self, question: str) -> Tuple[str, List[RetrievedContext]]:
        contexts = self.retriever.retrieve(question)
        if not contexts:
            return "Нет данных в предоставленном документе.", []

        if self._low_confidence_context(question, contexts):
            return "Нет данных в предоставленном документе.", contexts

        factoid_mode = is_factoid_query(question)
        context_block = "\n\n".join(
            f"[SOURCE {ctx.chunk_id}] {ctx.text}" for ctx in contexts
        )
        answer_instruction = (
            "Дай точный короткий ответ на русском языке (1-2 предложения), "
            "только по фактам из контекста, без интерпретаций и внешних добавлений."
            if factoid_mode
            else "Сформулируй точный ответ на русском языке (2-6 предложений)."
        )
        user_prompt = (
            f"Вопрос:\n{question}\n\n"
            f"Контекст:\n{context_block}\n\n"
            f"{answer_instruction}"
        )
        answer = self.client.generate(
            model_id=self.model_id,
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            seed=self.config.seed,
        )

        if self._looks_like_no_data(answer) and self._has_direct_evidence(question, contexts):
            repair_prompt = (
                f"Вопрос:\n{question}\n\n"
                f"Контекст:\n{context_block}\n\n"
                "В контексте есть прямые сведения для ответа. "
                "Дай короткий фактический ответ на русском языке (1-2 предложения), "
                "не пиши про отсутствие данных и не добавляй внешнюю информацию."
            )
            repaired = self.client.generate(
                model_id=self.model_id,
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=repair_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                seed=self.config.seed,
            )
            if repaired.strip():
                answer = repaired

        return answer, contexts


def build_pipeline(config: AppConfig, pdf_path: Path) -> RAGPipeline:
    text = extract_pdf_text(pdf_path)
    chunks = split_chunks(text, chunk_size=config.chunk_size, overlap=config.overlap)
    header_chunk = extract_header_chunk(pdf_path)
    if header_chunk:
        chunks = [header_chunk] + chunks
    if SentenceTransformer is not None and CrossEncoder is not None and BM25Okapi is not None and faiss is not None:
        print("Retriever mode: hybrid (bge-m3 + bm25 + rrf + reranker)")
        retriever = HybridRetriever(chunks=chunks, config=config)
    else:
        print("Retriever mode: lexical fallback (missing optional ML dependencies)")
        retriever = LexicalFallbackRetriever(chunks=chunks, config=config)
    client = OllamaClient(config=config)
    return RAGPipeline(config=config, retriever=retriever, client=client)


def run_batch(
    pipeline: RAGPipeline,
    questions: Sequence[str],
    answers_out: Path,
    answers_header: bool,
    debug_out: Path | None,
) -> None:
    answers: List[str] = []
    debug_rows: List[Dict[str, str]] = []

    for question in questions:
        answer, contexts = pipeline.answer(question)
        answers.append(answer)
        debug_rows.append(
            {
                "question": question,
                "answer": answer,
                "sources": json.dumps([ctx.chunk_id for ctx in contexts], ensure_ascii=False),
                "rerank_scores": json.dumps(
                    [round(ctx.rerank_score, 6) for ctx in contexts], ensure_ascii=False
                ),
            }
        )

    write_answers_only(path=answers_out, answers=answers, with_header=answers_header)
    if debug_out is not None:
        write_debug_csv(path=debug_out, rows=debug_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG pipeline with Ollama generation")
    parser.add_argument(
        "--pdf",
        type=Path,
        default=Path("Национальная_стратегия_развития_ИИ_2024.pdf"),
        help="Path to source PDF",
    )
    parser.add_argument("--question", type=str, help="Single question mode")
    parser.add_argument("--questions-file", type=Path, help="Batch mode: .txt or .csv")
    parser.add_argument(
        "--answers-out",
        type=Path,
        default=Path("answers_submission.csv"),
        help="Output CSV with answers-only",
    )
    parser.add_argument(
        "--answers-no-header",
        action="store_true",
        help="Write answers CSV without header row",
    )
    parser.add_argument(
        "--debug-out",
        type=Path,
        default=Path("answers_debug.csv"),
        help="Optional debug CSV with question/sources/scores",
    )
    parser.add_argument(
        "--no-debug-out",
        action="store_true",
        help="Disable debug CSV generation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.pdf.exists():
        raise FileNotFoundError(f"PDF not found: {args.pdf}")

    config = AppConfig.from_env()
    pipeline = build_pipeline(config=config, pdf_path=args.pdf)
    print(f"Using model: {pipeline.model_id}")

    if args.question:
        answer, contexts = pipeline.answer(args.question)
        print(answer)
        print("sources:", [ctx.chunk_id for ctx in contexts])
        return 0

    if args.questions_file:
        questions = read_questions(args.questions_file)
        if not questions:
            raise ValueError("No questions found in input file")
        debug_out = None if args.no_debug_out else args.debug_out
        run_batch(
            pipeline=pipeline,
            questions=questions,
            answers_out=args.answers_out,
            answers_header=not args.answers_no_header,
            debug_out=debug_out,
        )
        print(f"Answered: {len(questions)}")
        print(f"Submission file: {args.answers_out}")
        if debug_out is not None:
            print(f"Debug file: {debug_out}")
        return 0

    raise ValueError("Provide either --question or --questions-file")


if __name__ == "__main__":
    raise SystemExit(main())
