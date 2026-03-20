"""
RAG система для хранения и поиска знаний
"""
import os
import json
import logging
import threading
import functools
import hashlib
import re
from typing import Any, List, Dict, Optional
from datetime import datetime
from collections import defaultdict
from urllib.parse import unquote
import numpy as np
from shared.database import Base, Session, KnowledgeBase, KnowledgeChunk, KnowledgeImportLog, engine, get_session
from sqlalchemy import text, or_
from shared.qdrant_backend import QdrantBackend
try:
    from shared.rag_pipeline.embedder import embed_texts as pipeline_embed_texts
except Exception:
    pipeline_embed_texts = None

logger = logging.getLogger(__name__)

# Глобальный lock для всех операций записи в БД (SQLite не любит конкурирующие writers)
_db_write_lock = threading.Lock()

HAS_EMBEDDINGS = False
HAS_RERANKER = False
try:
    from sentence_transformers import SentenceTransformer, CrossEncoder
    import faiss
    HAS_EMBEDDINGS = True
    # Подавить предупреждение о hf_xet, если пакет не установлен
    import warnings
    warnings.filterwarnings('ignore', message='.*hf_xet.*', category=RuntimeWarning)
except ImportError:
    logger.warning("sentence-transformers и faiss не установлены. RAG будет работать в упрощенном режиме.")

# pymorphy для морфологической нормализации (optional, graceful degradation)
# Поддерживается pymorphy3 (Python 3.11+) и pymorphy2 (fallback)
HAS_PYMORPHY = False
_morph = None
try:
    import pymorphy3 as _pymorphy_module
    _morph = _pymorphy_module.MorphAnalyzer()
    HAS_PYMORPHY = True
except ImportError:
    try:
        import pymorphy2 as _pymorphy_module  # type: ignore[no-redef]
        _morph = _pymorphy_module.MorphAnalyzer()
        HAS_PYMORPHY = True
    except (ImportError, AttributeError):
        pass

_RU_STOP_WORDS = frozenset({
    "и", "в", "не", "на", "что", "как", "это", "он", "она", "они",
    "то", "с", "по", "из", "а", "но", "за", "до", "от", "при",
    "или", "же", "о", "об", "так", "ещё", "бы", "уже", "если",
    "его", "её", "им", "их", "нет", "да", "нам", "вам", "всё",
    "всех", "один", "два", "три", "год", "лет", "только", "также",
    "более", "может", "этого", "этому", "этим", "этой", "эти",
    "для", "над", "под", "без", "через", "между", "после",
    "перед", "чтобы", "когда", "потому", "где", "был", "была",
    "были", "есть", "быть", "будет", "который", "которая",
    "которые", "которого",
})
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+", flags=re.UNICODE)
_SECTION_SEPARATOR_RE = re.compile(r"\s*>\s*")
_CONTEXT_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}", flags=re.UNICODE)
_CONTEXT_LIST_SPLIT_RE = re.compile(r"(?:\r?\n|;\s+)", flags=re.UNICODE)
_CREDENTIAL_URL_RE = re.compile(r"([a-z][a-z0-9+\-.]*://)([^/@:\s]+):([^/@\s]+)@", flags=re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(?i)\b(authorization\b\s*:\s*(?:bearer|basic|token)\s+)([^\s,;]+)")
_BEARER_TOKEN_RE = re.compile(r"(?i)\b(bearer\s+)([^\s,;]+)")
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|api[_-]?key|secret)\b(\s*[:=]\s*)([^\s,;]+)"
)
_CONTEXT_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_EN_QUERY_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "this",
    "to", "use", "using", "what", "when", "where", "which", "with", "your",
})


def _coerce_non_negative_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced >= 0 else None


def _normalize_section_path_norm_value(section_path: str) -> str:
    normalized = str(section_path or "").replace("\\", "/").strip()
    normalized = _SECTION_SEPARATOR_RE.sub(" > ", normalized)
    normalized = _SPACE_RE.sub(" ", normalized).strip().lower()
    return normalized or "root"


def _estimate_token_count_value(content: str) -> int:
    return len(_WORD_RE.findall(content or ""))


def _tokenize_focus_terms(text: str) -> List[str]:
    tokens = []
    seen = set()
    for token in _WORD_RE.findall((text or "").lower()):
        if len(token) < 3:
            continue
        if token in _RU_STOP_WORDS:
            continue
        normalized = _normalize_ru(token) if HAS_PYMORPHY else token
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def _clip_excerpt(text: str, max_length: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_length:
        return normalized
    cut_point = normalized.rfind("\n", 0, max_length)
    if cut_point < max_length * 0.6:
        cut_point = normalized.rfind(" ", 0, max_length)
    if cut_point < max_length * 0.6:
        cut_point = max_length
    return normalized[:cut_point].rstrip() + "..."


def _split_context_units(text: str, chunk_kind: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if chunk_kind == "list":
        parts = _CONTEXT_LIST_SPLIT_RE.split(raw)
        units = [part.strip(" -•\t") for part in parts if part and part.strip(" -•\t")]
        return units or [raw]
    if chunk_kind in {"code", "code_file"}:
        lines = [line.rstrip() for line in raw.splitlines() if line.strip()]
        return lines or [raw]
    parts = _CONTEXT_SENTENCE_SPLIT_RE.split(raw)
    units = [part.strip() for part in parts if part and part.strip()]
    if len(units) <= 1 and ";" in raw:
        parts = _CONTEXT_LIST_SPLIT_RE.split(raw)
        units = [part.strip() for part in parts if part and part.strip()]
    merged_units: List[str] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if re.fullmatch(r"\d+[.)]?", unit) and index + 1 < len(units):
            merged_units.append(f"{unit} {units[index + 1]}".strip())
            index += 2
            continue
        merged_units.append(unit)
        index += 1
    return merged_units or [raw]


def _score_context_unit(unit: str, *, focus_terms: List[str], number_tokens: List[str]) -> float:
    lower = unit.lower()
    tokens = set(_tokenize_focus_terms(lower))
    score = float(len(tokens.intersection(focus_terms)))
    for number in number_tokens:
        if number in lower:
            score += 2.0
        if re.search(rf"(?:^|\s){re.escape(number)}[.)]", lower):
            score += 1.0
    if any(term in lower for term in focus_terms[:6]):
        score += 0.5
    return score


def describe_context_chunk(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = row or {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    content = str(payload.get("content") or "")
    source_path = str(payload.get("source_path") or "").strip()
    source_type = str(payload.get("source_type") or metadata.get("type") or "unknown").strip() or "unknown"
    doc_title = str(metadata.get("doc_title") or metadata.get("title") or source_path or "Без названия").strip() or "Без названия"
    section_title = str(metadata.get("section_title") or metadata.get("title") or doc_title).strip() or doc_title
    section_path = str(metadata.get("section_path") or doc_title or source_path or "ROOT").strip() or "ROOT"
    section_path_norm = str(metadata.get("section_path_norm") or _normalize_section_path_norm_value(section_path)).strip() or "root"
    chunk_kind = str(metadata.get("chunk_kind") or metadata.get("chunk_type") or metadata.get("block_type") or "text").strip() or "text"
    block_type = str(metadata.get("block_type") or chunk_kind or "text").strip() or "text"
    row_id = _coerce_non_negative_int(payload.get("id", payload.get("chunk_id")))
    chunk_no = _coerce_non_negative_int(payload.get("chunk_no", metadata.get("chunk_no")))
    page_no = _coerce_non_negative_int(metadata.get("page_no", metadata.get("page")))
    token_count_est = _coerce_non_negative_int(metadata.get("token_count_est")) or _estimate_token_count_value(content)
    chunk_hash = str(metadata.get("chunk_hash") or "").strip()
    if not chunk_hash:
        hash_basis = "\n".join(
            [
                source_type,
                source_path,
                str(chunk_no or 0),
                _SPACE_RE.sub(" ", content).strip(),
            ]
        )
        chunk_hash = hashlib.sha256(hash_basis.encode("utf-8", errors="ignore")).hexdigest()
    doc_key = (source_path or doc_title).strip().lower() or source_type.lower()
    if section_path_norm and section_path_norm != "root":
        scope_key = f"section:{section_path_norm}"
    elif page_no is not None:
        scope_key = f"page:{page_no}"
    else:
        scope_key = f"doc:{doc_key}"
    identity = f"id:{row_id}" if row_id is not None else f"{doc_key}#chunk:{chunk_no or 0}#{chunk_hash[:12]}"
    return {
        "id": row_id,
        "source_path": source_path,
        "source_type": source_type,
        "doc_title": doc_title,
        "doc_key": doc_key,
        "section_title": section_title,
        "section_path": section_path,
        "section_path_norm": section_path_norm,
        "scope_key": scope_key,
        "chunk_kind": chunk_kind,
        "block_type": block_type,
        "chunk_no": chunk_no,
        "page_no": page_no,
        "chunk_hash": chunk_hash,
        "token_count_est": token_count_est,
        "identity": identity,
        "content": content,
    }


def _candidate_rank_score(candidate: Dict[str, Any]) -> float:
    for key in ("multi_query_score", "rerank_score", "rank_score"):
        value = candidate.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    try:
        distance = float(candidate.get("distance", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return -distance


def _candidate_stable_key(candidate: Dict[str, Any]) -> str:
    info = describe_context_chunk(candidate)
    identity = str(info.get("identity") or "").strip()
    if identity:
        return identity
    return f"{candidate.get('source_path') or ''}::{str(candidate.get('content') or '')[:200]}"


def _candidate_family_key(candidate: Dict[str, Any]) -> str:
    info = describe_context_chunk(candidate)
    doc_key = str(info.get("doc_key") or "").strip()
    scope_key = str(info.get("scope_key") or "").strip()
    if doc_key and scope_key:
        return f"{doc_key}::{scope_key}"
    if doc_key:
        return f"{doc_key}::doc"
    return str(info.get("identity") or "").strip()


def _normalize_field_text_value(text: str) -> str:
    decoded = unquote(str(text or ""))
    lowered = decoded.lower().replace("\\", " ").replace("/", " ").replace("_", " ")
    normalized = re.sub(r"[^a-zа-яё0-9]+", " ", lowered, flags=re.IGNORECASE)
    return _SPACE_RE.sub(" ", normalized).strip()


def _normalized_field_terms(text: str) -> List[str]:
    normalized = _normalize_field_text_value(text)
    return [token for token in normalized.split(" ") if token]


def _extract_query_specificity_terms(query: str) -> List[str]:
    terms: List[str] = []
    seen = set()
    for token in _WORD_RE.findall((query or "").lower()):
        if len(token) < 3:
            continue
        if token in _RU_STOP_WORDS or token in _EN_QUERY_STOP_WORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _candidate_field_text(candidate: Dict[str, Any]) -> str:
    info = describe_context_chunk(candidate)
    parts = [
        info.get("source_path") or "",
        info.get("doc_title") or "",
        info.get("section_title") or "",
        info.get("section_path") or "",
    ]
    return _normalize_field_text_value(" ".join(str(part or "") for part in parts if part))


def _field_match_metrics(field_text: str, query_terms: List[str], normalized_query: str) -> Dict[str, Any]:
    normalized_field = _normalize_field_text_value(field_text)
    if not normalized_field:
        return {
            "matched_terms": [],
            "coverage_ratio": 0.0,
            "precision_ratio": 0.0,
            "exact_match": False,
        }

    field_terms = _normalized_field_terms(normalized_field)
    matched_terms = [term for term in query_terms if term in normalized_field]
    unique_hits = len({term for term in matched_terms})
    coverage_ratio = float(unique_hits / float(len(query_terms) or 1))
    precision_ratio = float(unique_hits / float(len(field_terms) or 1))
    exact_match = bool(normalized_query and normalized_query in normalized_field)
    return {
        "matched_terms": matched_terms,
        "coverage_ratio": coverage_ratio,
        "precision_ratio": precision_ratio,
        "exact_match": exact_match,
    }


def _annotate_candidates_with_query_field_specificity(
    candidates: List[Dict[str, Any]],
    *,
    query: str,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    query_terms = _extract_query_specificity_terms(query)
    if not query_terms:
        return [dict(candidate) for candidate in candidates]

    normalized_query = _normalize_field_text_value(query)
    candidate_field_texts = [_candidate_field_text(candidate) for candidate in candidates]
    total_candidates = max(1, len(candidate_field_texts))
    term_doc_freq: Dict[str, int] = {}
    for term in query_terms:
        term_doc_freq[term] = sum(1 for text in candidate_field_texts if term in text)

    distinctive_df_threshold = min(2, total_candidates)
    annotated: List[Dict[str, Any]] = []
    for row, field_text in zip(candidates, candidate_field_texts):
        candidate = dict(row)
        matched_terms = [term for term in query_terms if term in field_text]
        distinctive_matches = [term for term in matched_terms if term_doc_freq.get(term, 0) <= distinctive_df_threshold]
        specificity_score = 0.0
        for term in matched_terms:
            doc_freq = max(1, int(term_doc_freq.get(term, 0)))
            specificity_score += float(np.log((total_candidates + 1.0) / (doc_freq + 1.0)) + 1.0)
        exact_match = bool(normalized_query and normalized_query in field_text)
        info = describe_context_chunk(candidate)
        field_priority = {
            "doc_title": 1.05,
            "section_title": 1.15,
            "section_path": 0.95,
            "source_path": 0.75,
        }
        best_field_name = ""
        best_coverage = 0.0
        best_precision = 0.0
        best_exact = False
        field_focus_score = 0.0
        for field_name, weight in field_priority.items():
            metrics = _field_match_metrics(str(info.get(field_name) or ""), query_terms, normalized_query)
            coverage = float(metrics["coverage_ratio"])
            precision = float(metrics["precision_ratio"])
            if metrics["exact_match"] or coverage > 0.0:
                field_focus_score += (
                    (3.0 if metrics["exact_match"] else 0.0)
                    + (2.2 * coverage)
                    + (1.8 * precision)
                ) * weight
            if (
                bool(metrics["exact_match"]) > best_exact
                or coverage > best_coverage
                or (coverage == best_coverage and precision > best_precision)
            ):
                best_field_name = field_name
                best_coverage = coverage
                best_precision = precision
                best_exact = bool(metrics["exact_match"])

        content_anchor = _normalize_field_text_value(
            " ".join(
                [
                    str(info.get("section_title") or ""),
                    str(info.get("section_path") or ""),
                    str(candidate.get("content") or "")[:500],
                ]
            )
        )
        content_focus_hits = sum(1 for term in query_terms if term in content_anchor)
        content_focus_score = float(
            min(1.5, 0.3 * content_focus_hits)
            + (0.8 if normalized_query and normalized_query in content_anchor else 0.0)
        )
        candidate["_query_field_term_hits"] = int(len(matched_terms))
        candidate["_query_field_coverage_ratio"] = float(len(matched_terms) / float(len(query_terms)))
        candidate["_query_field_distinctive_hits"] = int(len(distinctive_matches))
        candidate["_query_field_exact_match"] = exact_match
        candidate["_query_field_best_field"] = best_field_name
        candidate["_query_field_best_coverage"] = float(best_coverage)
        candidate["_query_field_best_precision"] = float(best_precision)
        candidate["_query_field_best_exact"] = bool(best_exact)
        candidate["_query_field_content_hits"] = int(content_focus_hits)
        candidate["_query_field_specificity_score"] = float(
            specificity_score
            + (0.75 * len(matched_terms))
            + (1.5 * len(distinctive_matches))
            + (3.0 if exact_match else 0.0)
            + field_focus_score
            + content_focus_score
        )
        annotated.append(candidate)
    return annotated


def _order_candidates_by_query_field_specificity(
    candidates: List[Dict[str, Any]],
    *,
    query: str,
) -> List[Dict[str, Any]]:
    annotated = _annotate_candidates_with_query_field_specificity(candidates, query=query)
    def _has_candidate_signal(candidate: Dict[str, Any]) -> bool:
        return (
            bool(candidate.get("_query_field_exact_match"))
            or bool(candidate.get("_query_field_best_exact"))
            or int(candidate.get("_query_field_distinctive_hits", 0)) >= 1
            or float(candidate.get("_query_field_best_coverage", 0.0)) > 0.0
            or float(candidate.get("_query_field_coverage_ratio", 0.0)) > 0.0
        )

    has_specific_signal = any(
        bool(candidate.get("_query_field_exact_match"))
        or bool(candidate.get("_query_field_best_exact"))
        or (
            int(candidate.get("_query_field_distinctive_hits", 0)) >= 1
            and int(candidate.get("_query_field_term_hits", 0)) >= 2
        )
        or (
            float(candidate.get("_query_field_coverage_ratio", 0.0)) >= 0.8
            and int(candidate.get("_query_field_term_hits", 0)) >= 3
        )
        or (
            float(candidate.get("_query_field_best_coverage", 0.0)) >= 0.6
            and float(candidate.get("_query_field_best_precision", 0.0)) >= 0.25
        )
        for candidate in annotated
    )
    if not has_specific_signal:
        return annotated

    indexed = list(enumerate(annotated))
    signaled = [(idx, candidate) for idx, candidate in indexed if _has_candidate_signal(candidate)]
    unsignaled = [candidate for _, candidate in indexed if not _has_candidate_signal(candidate)]
    signaled.sort(
        key=lambda item: (
            -int(bool(item[1].get("_query_field_exact_match"))),
            -int(bool(item[1].get("_query_field_best_exact"))),
            -float(item[1].get("_query_field_best_coverage", 0.0)),
            -float(item[1].get("_query_field_best_precision", 0.0)),
            -float(item[1].get("_query_field_coverage_ratio", 0.0)),
            -int(item[1].get("_query_field_distinctive_hits", 0)),
            -float(item[1].get("_query_field_specificity_score", 0.0)),
            int(item[1].get("_family_rank", 999999)),
            -int(item[1].get("_family_channel_count", 0)),
            -int(item[1].get("_family_candidate_count", 0)),
            -float(item[1].get("_family_support_rrf", 0.0)),
            -_candidate_rank_score(item[1]),
            item[0],
        )
    )
    return [candidate for _, candidate in signaled] + unsignaled


def _query_prefers_broad_status_like_sources(query: str) -> bool:
    query_lower = _normalize_field_text_value(query)
    if not query_lower:
        return False
    status_markers = (
        "status",
        "statuses",
        "archive",
        "archived",
        "history",
        "historical",
        "backlog",
        "roadmap",
        "release",
        "releases",
        "changelog",
        "inventory",
        "matrix",
        "overview",
        "summary",
        "note",
        "notes",
        "статус",
        "архив",
        "история",
        "релиз",
        "обзор",
        "заметки",
        "список",
    )
    return any(marker in query_lower for marker in status_markers)


def _candidate_canonicality_broad_markers(candidate: Dict[str, Any]) -> int:
    info = describe_context_chunk(candidate)
    marker_text = " ".join(
        [
            str(candidate.get("source_path") or ""),
            str(info.get("doc_title") or ""),
            str(info.get("section_title") or ""),
            str(info.get("section_path") or ""),
        ]
    ).lower()
    broad_markers = (
        "status",
        "archive",
        "history",
        "historical",
        "overview",
        "introduction",
        "summary",
        "notes",
        "note",
        "changelog",
        "release note",
        "release notes",
        "faq",
        "matrix",
        "inventory",
        "backlog",
        "roadmap",
        "log",
        "статус",
        "архив",
        "история",
        "обзор",
        "заметки",
        "сводка",
        "список",
    )
    return sum(1 for marker in broad_markers if marker in marker_text)


def _annotate_candidates_with_canonicality(
    candidates: List[Dict[str, Any]],
    *,
    query: str,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    query_terms = _extract_query_specificity_terms(query)
    prefers_broad_status_like = _query_prefers_broad_status_like_sources(query)
    annotated: List[Dict[str, Any]] = []

    for row in candidates:
        candidate = dict(row)
        info = describe_context_chunk(candidate)
        content = str(candidate.get("content") or "")
        content_sample = content[:900]
        lead_sample = content[:280]
        normalized_content = _normalize_field_text_value(content_sample)
        normalized_lead = _normalize_field_text_value(lead_sample)
        source_path = str(candidate.get("source_path") or "")
        section_path = str(info.get("section_path") or "")
        structural_scope = " ".join(
            [
                source_path,
                str(info.get("doc_title") or ""),
                str(info.get("section_title") or ""),
                section_path,
            ]
        )

        field_term_hits = int(candidate.get("_query_field_term_hits", 0))
        distinctive_hits = int(candidate.get("_query_field_distinctive_hits", 0))
        best_coverage = float(candidate.get("_query_field_best_coverage", 0.0))
        best_precision = float(candidate.get("_query_field_best_precision", 0.0))
        specificity_score = float(candidate.get("_query_field_specificity_score", 0.0))
        family_channel_count = int(candidate.get("_family_channel_count", 0))
        family_candidate_count = int(candidate.get("_family_candidate_count", 0))
        family_support_rrf = float(candidate.get("_family_support_rrf", 0.0))
        exactish = bool(candidate.get("_query_field_exact_match")) or bool(candidate.get("_query_field_best_exact"))

        lead_hits = sum(1 for term in query_terms if term in normalized_lead)
        content_hits = sum(1 for term in query_terms if term in normalized_content)
        spread_hits = max(0, content_hits - field_term_hits)

        content_lines = [line.strip() for line in content_sample.splitlines() if line.strip()]
        line_count = max(1, len(content_lines))
        bulletish_lines = sum(
            1
            for line in content_lines
            if re.match(r"^([-*#]|\d+[.)]|[A-Z0-9_]{3,}\s*[:=-])", line)
            or "|" in line
        )
        list_density = float(bulletish_lines / float(line_count))

        structural_tokens = _WORD_RE.findall(structural_scope)
        content_tokens = _WORD_RE.findall(content_sample)
        token_count = max(1, len(structural_tokens) + len(content_tokens))
        identifier_noise = sum(
            1
            for token in structural_tokens + content_tokens
            if re.fullmatch(r"[A-Z0-9_]{4,}", token or "")
        )
        identifier_density = float(identifier_noise / float(token_count))

        section_depth = max(
            section_path.count(">") + 1 if section_path else 0,
            source_path.count("/") if source_path else 0,
        )
        broad_markers = _candidate_canonicality_broad_markers(candidate)

        canonicality_score = 0.0
        canonicality_reasons: List[str] = []
        if exactish:
            canonicality_score += 3.0
            canonicality_reasons.append("exact_field_match")
        if best_coverage >= 0.55:
            canonicality_score += 1.6 * best_coverage
            canonicality_reasons.append("focused_field_coverage")
        if best_precision >= 0.25:
            canonicality_score += 1.4 * best_precision
            canonicality_reasons.append("field_precision")
        if lead_hits:
            canonicality_score += min(1.2, 0.35 * lead_hits)
            canonicality_reasons.append("lead_match")
        if distinctive_hits:
            canonicality_score += min(1.2, 0.45 * distinctive_hits)
            canonicality_reasons.append("distinctive_terms")
        if field_term_hits >= 2:
            canonicality_score += min(1.0, 0.3 * field_term_hits)
            canonicality_reasons.append("field_term_concentration")
        if family_channel_count >= 2 or family_candidate_count >= 2:
            canonicality_score += min(1.2, 0.35 * family_channel_count + 0.2 * family_candidate_count)
            canonicality_reasons.append("family_support")
        if family_support_rrf > 0.0 and (exactish or field_term_hits > 0 or best_coverage > 0.0):
            canonicality_score += min(0.8, family_support_rrf * 4.0)
        if section_depth and section_depth <= 4 and (exactish or field_term_hits > 0 or best_coverage > 0.0):
            canonicality_score += 0.2
            canonicality_reasons.append("bounded_scope")

        contamination_penalty = 0.0
        contamination_reasons: List[str] = []
        if list_density >= 0.34:
            contamination_penalty += min(1.1, list_density * 1.6)
            contamination_reasons.append("list_or_table_shape")
        if identifier_density >= 0.06:
            contamination_penalty += min(1.0, identifier_density * 8.0)
            contamination_reasons.append("identifier_density")
        if spread_hits >= 2 and best_precision < 0.3:
            contamination_penalty += min(1.1, 0.45 * spread_hits)
            contamination_reasons.append("diffuse_query_match")
        if broad_markers and not prefers_broad_status_like and best_coverage < 0.8:
            contamination_penalty += min(1.2, 0.35 * broad_markers)
            contamination_reasons.append("broad_scope_marker")
        if field_term_hits == 0 and content_hits >= 2:
            contamination_penalty += 0.5
            contamination_reasons.append("content_only_match")
        if (
            not exactish
            and family_channel_count <= 1
            and family_candidate_count <= 1
            and best_coverage < 0.45
            and specificity_score < 4.0
            and (field_term_hits > 0 or content_hits > 0 or distinctive_hits > 0)
        ):
            contamination_penalty += 0.35
            contamination_reasons.append("weak_family_support")

        if exactish or best_coverage >= 0.85:
            contamination_penalty = max(0.0, contamination_penalty - 0.55)
        if lead_hits >= 2:
            contamination_penalty = max(0.0, contamination_penalty - 0.25)

        candidate["_canonicality_score"] = float(canonicality_score)
        candidate["_canonicality_reason"] = ",".join(canonicality_reasons[:4])
        candidate["_contamination_penalty"] = float(contamination_penalty)
        candidate["_contamination_reason"] = ",".join(contamination_reasons[:4])
        candidate["_canonicality_net_score"] = float(canonicality_score - contamination_penalty)
        annotated.append(candidate)

    return annotated


def _order_candidates_by_canonicality(
    candidates: List[Dict[str, Any]],
    *,
    query: str,
) -> List[Dict[str, Any]]:
    annotated = _annotate_candidates_with_canonicality(candidates, query=query)
    has_signal = any(
        bool(candidate.get("_query_field_exact_match"))
        or bool(candidate.get("_query_field_best_exact"))
        or float(candidate.get("_query_field_best_coverage", 0.0)) >= 0.55
        or int(candidate.get("_query_field_distinctive_hits", 0)) >= 1
        or float(candidate.get("_contamination_penalty", 0.0)) >= 0.35
        for candidate in annotated
    )
    if not has_signal:
        return annotated

    indexed = list(enumerate(annotated))
    signaled = [
        (idx, candidate)
        for idx, candidate in indexed
        if float(candidate.get("_canonicality_score", 0.0)) > 0.0
        or float(candidate.get("_contamination_penalty", 0.0)) > 0.0
    ]
    unsignaled = [candidate for _, candidate in indexed if not (
        float(candidate.get("_canonicality_score", 0.0)) > 0.0
        or float(candidate.get("_contamination_penalty", 0.0)) > 0.0
    )]
    signaled.sort(
        key=lambda item: (
            -float(item[1].get("_canonicality_net_score", 0.0)),
            -float(item[1].get("_canonicality_score", 0.0)),
            float(item[1].get("_contamination_penalty", 0.0)),
            -int(bool(item[1].get("_query_field_exact_match"))),
            -int(bool(item[1].get("_query_field_best_exact"))),
            -float(item[1].get("_query_field_best_coverage", 0.0)),
            -float(item[1].get("_query_field_best_precision", 0.0)),
            int(item[1].get("_family_rank", 999999)),
            -int(item[1].get("_family_channel_count", 0)),
            -int(item[1].get("_family_candidate_count", 0)),
            -float(item[1].get("_family_support_rrf", 0.0)),
            -_candidate_rank_score(item[1]),
            item[0],
        )
    )
    return [item[1] for item in signaled] + unsignaled


def _annotate_candidates_with_family_support(
    candidates: List[Dict[str, Any]],
    *,
    channel_candidates: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    rrf_k: int = 20,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    k = max(1, int(rrf_k))
    family_stats: Dict[str, Dict[str, Any]] = {}

    for channel_name, rows in (channel_candidates or {}).items():
        channel = str(channel_name or "unknown").strip().lower() or "unknown"
        for rank, row in enumerate(rows or [], start=1):
            if not isinstance(row, dict):
                continue
            family_key = _candidate_family_key(row)
            if not family_key:
                continue
            stats = family_stats.setdefault(
                family_key,
                {
                    "channels": set(),
                    "candidate_keys": set(),
                    "support_rrf": 0.0,
                    "best_rank": rank,
                },
            )
            stats["channels"].add(channel)
            stats["candidate_keys"].add(_candidate_stable_key(row))
            stats["support_rrf"] = float(stats.get("support_rrf", 0.0)) + (1.0 / float(k + rank))
            stats["best_rank"] = min(int(stats.get("best_rank", rank)), rank)

    if not family_stats:
        for row in candidates:
            family_key = _candidate_family_key(row)
            if not family_key:
                continue
            family_stats.setdefault(
                family_key,
                {
                    "channels": {str(row.get("origin") or "unknown").strip().lower() or "unknown"},
                    "candidate_keys": {_candidate_stable_key(row)},
                    "support_rrf": 1.0 / float(k + 1),
                    "best_rank": 1,
                },
            )

    ordered_family_keys = sorted(
        family_stats.keys(),
        key=lambda key: (
            len(family_stats[key].get("channels", set())),
            len(family_stats[key].get("candidate_keys", set())),
            float(family_stats[key].get("support_rrf", 0.0)),
            -int(family_stats[key].get("best_rank", 999999)),
        ),
        reverse=True,
    )
    family_rank = {key: rank for rank, key in enumerate(ordered_family_keys, start=1)}

    annotated: List[Dict[str, Any]] = []
    for row in candidates:
        candidate = dict(row)
        family_key = _candidate_family_key(candidate)
        stats = family_stats.get(family_key, {})
        candidate["_family_key"] = family_key
        candidate["_family_rank"] = int(family_rank.get(family_key, len(ordered_family_keys) + 1))
        candidate["_family_channel_count"] = int(len(stats.get("channels", set())))
        candidate["_family_candidate_count"] = int(len(stats.get("candidate_keys", set())))
        candidate["_family_support_rrf"] = float(stats.get("support_rrf", 0.0))
        annotated.append(candidate)
    return annotated


def _order_candidates_by_family_support(
    candidates: List[Dict[str, Any]],
    *,
    channel_candidates: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    rrf_k: int = 20,
) -> List[Dict[str, Any]]:
    annotated = _annotate_candidates_with_family_support(
        candidates,
        channel_candidates=channel_candidates,
        rrf_k=rrf_k,
    )
    has_supported_family = any(
        int(candidate.get("_family_channel_count", 0)) >= 2 or int(candidate.get("_family_candidate_count", 0)) >= 2
        for candidate in annotated
    )
    if not has_supported_family:
        return annotated
    indexed = list(enumerate(annotated))
    indexed.sort(
        key=lambda item: (
            int(item[1].get("_family_rank", 999999)),
            -_candidate_rank_score(item[1]),
            -int(item[1].get("_family_channel_count", 0)),
            -int(item[1].get("_family_candidate_count", 0)),
            -float(item[1].get("_family_support_rrf", 0.0)),
            item[0],
        )
    )
    return [item[1] for item in indexed]


def _group_chunk_embeddings_by_kb(chunks: List[Any]) -> Dict[str, Any]:
    chunks_by_kb = defaultdict(list)
    all_chunks_by_kb = defaultdict(list)
    expected_dim_by_kb: Dict[int, int] = {}
    dim_mismatches_by_kb: Dict[int, int] = defaultdict(int)
    chunks_with_embedding = 0

    for chunk in chunks or []:
        kb_id = int(getattr(chunk, "knowledge_base_id", 0) or 0)
        all_chunks_by_kb[kb_id].append(chunk)
        raw_embedding = getattr(chunk, "embedding", None)
        if not raw_embedding:
            continue
        try:
            embedding = np.array(json.loads(raw_embedding), dtype="float32").reshape(-1)
        except Exception as e:
            logger.debug("Failed to parse embedding for chunk %s: %s", getattr(chunk, "id", None), e)
            continue
        if embedding.size == 0:
            continue

        embedding_dim = int(embedding.shape[0])
        expected_dim = expected_dim_by_kb.get(kb_id)
        if expected_dim is None:
            expected_dim_by_kb[kb_id] = embedding_dim
            expected_dim = embedding_dim
        if embedding_dim != expected_dim:
            dim_mismatches_by_kb[kb_id] += 1
            logger.warning(
                "Skipping chunk %s in KB %s: embedding dimension %s != expected %s",
                getattr(chunk, "id", None),
                kb_id,
                embedding_dim,
                expected_dim,
            )
            continue

        chunks_by_kb[kb_id].append((chunk, embedding))
        chunks_with_embedding += 1

    return {
        "chunks_by_kb": chunks_by_kb,
        "all_chunks_by_kb": all_chunks_by_kb,
        "expected_dim_by_kb": expected_dim_by_kb,
        "dim_mismatches_by_kb": dim_mismatches_by_kb,
        "chunks_with_embedding": chunks_with_embedding,
    }


def merge_multi_query_candidates(
    candidate_batches: List[List[Dict[str, Any]]],
    *,
    rrf_k: int = 20,
) -> List[Dict[str, Any]]:
    """Fuse bounded multi-query retrieval batches by stable chunk identity."""
    k = max(1, int(rrf_k))
    merged: Dict[str, Dict[str, Any]] = {}

    for batch in candidate_batches or []:
        for rank, candidate in enumerate(batch or [], start=1):
            if not isinstance(candidate, dict):
                continue
            row = dict(candidate)
            info = describe_context_chunk(row)
            identity = str(info.get("identity") or "").strip()
            if not identity:
                identity = f"{row.get('source_path') or ''}::{str(row.get('content') or '')[:200]}"
            mode = str(row.get("query_variant_mode") or "original").strip().lower() or "original"
            variant_query = str(row.get("query_variant_query") or "").strip()
            variant_reason = str(row.get("query_variant_reason") or "").strip()
            score = _candidate_rank_score(row)
            rrf_score = 1.0 / float(k + rank)

            entry = merged.get(identity)
            if entry is None:
                merged[identity] = {
                    "row": row,
                    "best_score": score,
                    "rrf_score": rrf_score,
                    "hit_count": 1,
                    "variant_modes": [mode],
                    "variant_queries": ([variant_query] if variant_query else []),
                    "variant_reasons": ([variant_reason] if variant_reason else []),
                }
                continue

            entry["rrf_score"] = float(entry.get("rrf_score", 0.0)) + rrf_score
            entry["hit_count"] = int(entry.get("hit_count", 1)) + 1
            variant_modes = entry.setdefault("variant_modes", [])
            if mode and mode not in variant_modes:
                variant_modes.append(mode)
            variant_queries = entry.setdefault("variant_queries", [])
            if variant_query and variant_query not in variant_queries:
                variant_queries.append(variant_query)
            variant_reasons = entry.setdefault("variant_reasons", [])
            if variant_reason and variant_reason not in variant_reasons:
                variant_reasons.append(variant_reason)

            best_score = float(entry.get("best_score", 0.0))
            current_mode = str(entry.get("row", {}).get("query_variant_mode") or "original").strip().lower() or "original"
            should_replace = score > best_score or (
                score == best_score and mode == "original" and current_mode != "original"
            )
            if should_replace:
                entry["row"] = row
                entry["best_score"] = score

    fused: List[Dict[str, Any]] = []
    for entry in merged.values():
        row = dict(entry.get("row") or {})
        best_score = float(entry.get("best_score", 0.0))
        rrf_score = float(entry.get("rrf_score", 0.0))
        row["multi_query_score"] = best_score + rrf_score
        row["multi_query_rrf"] = rrf_score
        row["multi_query_hit_count"] = int(entry.get("hit_count", 1))
        row["query_variant_modes"] = list(entry.get("variant_modes") or [])
        row["query_variant_queries"] = list(entry.get("variant_queries") or [])
        row["query_variant_reasons"] = list(entry.get("variant_reasons") or [])
        fused.append(row)

    fused.sort(
        key=lambda item: (
            _candidate_rank_score(item),
            int(item.get("multi_query_hit_count", 1)),
            float(item.get("multi_query_rrf", 0.0)),
        ),
        reverse=True,
    )
    return fused


def build_query_focused_excerpt(
    query: str,
    content: str,
    *,
    max_length: int,
    chunk_kind: str = "text",
) -> str:
    raw = str(content or "").strip()
    if not raw:
        return ""
    focus_terms = _tokenize_focus_terms(query)
    number_tokens = list(dict.fromkeys(_CONTEXT_NUMBER_RE.findall(query or "")))
    if chunk_kind in {"code", "code_file"}:
        return _clip_excerpt(raw, max_length)

    units = _split_context_units(raw, chunk_kind)
    if len(raw) <= max_length and (len(units) <= 1 or (not focus_terms and not number_tokens)):
        return raw
    if len(units) <= 1:
        return _clip_excerpt(raw, max_length)
    if not focus_terms and not number_tokens:
        return _clip_excerpt(raw, max_length)

    scored = [
        (_score_context_unit(unit, focus_terms=focus_terms, number_tokens=number_tokens), index, unit)
        for index, unit in enumerate(units)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_index, _best_unit = scored[0]
    if best_score <= 0:
        return _clip_excerpt(raw, max_length)

    chosen: List[tuple[int, str]] = [(best_index, units[best_index])]
    used_chars = len(units[best_index])
    for distance in range(1, len(units)):
        added = False
        for candidate_index in (best_index - distance, best_index + distance):
            if candidate_index < 0 or candidate_index >= len(units):
                continue
            if any(existing_index == candidate_index for existing_index, _ in chosen):
                continue
            unit = units[candidate_index]
            unit_score = _score_context_unit(unit, focus_terms=focus_terms, number_tokens=number_tokens)
            min_support_score = 0.0
            if number_tokens:
                min_support_score = max(1.5, best_score * 0.7)
            elif best_score > 1.5:
                min_support_score = best_score * 0.45
            if unit_score < min_support_score:
                continue
            projected = used_chars + len(unit) + 1
            if projected > max_length:
                continue
            chosen.append((candidate_index, unit))
            used_chars = projected
            added = True
        if not added and used_chars >= max_length * 0.75:
            break

    chosen.sort(key=lambda item: item[0])
    separator = "\n" if chunk_kind == "list" else " "
    excerpt = separator.join(unit for _, unit in chosen).strip()
    return _clip_excerpt(excerpt or raw, max_length)


@functools.lru_cache(maxsize=8192)
def _normalize_ru(token: str) -> str:
    """Нормализовать токен через pymorphy2 (с кешированием результатов)."""
    if HAS_PYMORPHY and _morph is not None:
        return _morph.parse(token)[0].normal_form
    return token


# Классы KnowledgeBase и KnowledgeChunk импортируются из database.py


class RAGSystem:
    """Система RAG для поиска в базе знаний"""
    
    def __init__(self, model_name: str = None):
        global HAS_EMBEDDINGS, HAS_RERANKER
        
        # Получить имя модели из конфига, если не указано
        if model_name is None:
            try:
                from shared.config import RAG_MODEL_NAME
                model_name = RAG_MODEL_NAME
            except ImportError:
                model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        
        self.model_name = model_name
        self.encoder = None
        self.index = None  # Устаревший: один индекс для всех KB
        self.chunks = []  # Устаревший: все чанки вместе
        # Индексы по базам знаний (для раздельного поиска)
        self.index_by_kb: Dict[int, faiss.Index] = {}
        self.index_dimension_by_kb: Dict[int, int] = {}
        self.chunks_by_kb: Dict[int, List[KnowledgeChunk]] = {}
        # BM25 индексы (in-memory)
        self.bm25_index_by_kb: Dict[int, Dict] = {}
        self.bm25_index_all: Optional[Dict] = None
        self.bm25_chunks_by_kb: Dict[int, List[KnowledgeChunk]] = {}
        self.bm25_chunks_all: List[KnowledgeChunk] = []
        # Сессии создаются на каждую операцию, не храним глобальную сессию
        self.reranker = None
        # Явные бюджеты retrieval-каналов; RAG_MAX_CANDIDATES сохраняется как legacy fallback.
        def _coerce_positive_int(value: Any, fallback: int) -> int:
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                return max(1, int(fallback))

        try:
            from shared.config import (
                RAG_ENABLE_RERANK,
                RAG_MAX_CANDIDATES,
                RAG_DENSE_CANDIDATES,
                RAG_BM25_CANDIDATES,
                RAG_RERANK_TOP_N,
            )
            self.enable_rerank = RAG_ENABLE_RERANK
            self.max_candidates = _coerce_positive_int(RAG_MAX_CANDIDATES, 100)
            self.dense_candidate_budget = _coerce_positive_int(RAG_DENSE_CANDIDATES, self.max_candidates)
            self.bm25_candidate_budget = _coerce_positive_int(RAG_BM25_CANDIDATES, self.max_candidates)
            self.rerank_top_n = _coerce_positive_int(
                RAG_RERANK_TOP_N,
                max(self.dense_candidate_budget, self.bm25_candidate_budget),
            )
        except ImportError:
            self.enable_rerank = os.getenv("RAG_ENABLE_RERANK", "true").lower() == "true"
            self.max_candidates = _coerce_positive_int(os.getenv("RAG_MAX_CANDIDATES", "100"), 100)
            self.dense_candidate_budget = _coerce_positive_int(
                os.getenv("RAG_DENSE_CANDIDATES", str(self.max_candidates)),
                self.max_candidates,
            )
            self.bm25_candidate_budget = _coerce_positive_int(
                os.getenv("RAG_BM25_CANDIDATES", str(self.max_candidates)),
                self.max_candidates,
            )
            self.rerank_top_n = _coerce_positive_int(
                os.getenv(
                    "RAG_RERANK_TOP_N",
                    str(max(self.dense_candidate_budget, self.bm25_candidate_budget)),
                ),
                max(self.dense_candidate_budget, self.bm25_candidate_budget),
            )

        # Retrieval backend switch (v3)
        self.retrieval_backend = "legacy"
        self.qdrant_backend: Optional[QdrantBackend] = None
        self._qdrant_bootstrap_done = False
        try:
            from shared.config import (
                RAG_BACKEND,
                QDRANT_URL,
                QDRANT_API_KEY,
                QDRANT_COLLECTION,
                QDRANT_TIMEOUT_SEC,
            )
            self.retrieval_backend = (RAG_BACKEND or "legacy").strip().lower()
            if self.retrieval_backend == "qdrant":
                self.qdrant_backend = QdrantBackend(
                    url=QDRANT_URL,
                    api_key=QDRANT_API_KEY,
                    collection=QDRANT_COLLECTION,
                    timeout_sec=QDRANT_TIMEOUT_SEC,
                )
        except Exception:
            self.retrieval_backend = os.getenv("RAG_BACKEND", "legacy").strip().lower() or "legacy"
            if self.retrieval_backend == "qdrant":
                self.qdrant_backend = QdrantBackend(
                    url=os.getenv("QDRANT_URL", ""),
                    api_key=os.getenv("QDRANT_API_KEY", ""),
                    collection=os.getenv("QDRANT_COLLECTION", "rag_chunks_v3"),
                    timeout_sec=float(os.getenv("QDRANT_TIMEOUT_SEC", "10")),
                )
        if self.retrieval_backend == "qdrant":
            if self.qdrant_backend and self.qdrant_backend.enabled:
                logger.info("✅ RAG backend selected: qdrant")
            else:
                logger.warning("⚠️ RAG_BACKEND=qdrant, but Qdrant is not configured. Falling back to legacy backend.")
                self.retrieval_backend = "legacy"
                self.qdrant_backend = None
        
        # Проверить, нужно ли загружать модель
        try:
            from shared.config import RAG_ENABLE
            if RAG_ENABLE is False:
                HAS_EMBEDDINGS = False
                logger.info("ℹ️ RAG отключен в конфигурации, будет использоваться простой поиск")
                return
        except ImportError:
            pass  # RAG_ENABLE не указан, продолжаем
        
        if HAS_EMBEDDINGS:
            try:
                # Определить путь к кэшу моделей (сохраняется между перезапусками)
                # Используем HF_HOME если установлен, иначе BOT_DATA_DIR
                cache_dir = os.getenv("HF_HOME") or os.path.join(os.getenv("BOT_DATA_DIR", "/app/data"), "cache", "huggingface")
                os.makedirs(cache_dir, exist_ok=True)
                
                # Проверить, есть ли модель в кэше
                import glob
                # sentence-transformers кеширует модели в cache_dir/models--model_name
                model_cache_name = model_name.replace("/", "--")
                model_cache_path = os.path.join(cache_dir, f"models--{model_cache_name}")
                
                if os.path.exists(model_cache_path):
                    logger.info(f"📥 Загрузка модели эмбеддингов из кэша: {model_name}")
                    logger.info(f"   Кэш: {model_cache_path}")
                else:
                    logger.info(f"📥 Загрузка модели эмбеддингов: {model_name}")
                    logger.info("   (Это может занять некоторое время при первом запуске)")
                    logger.info(f"   Кэш будет сохранен в: {cache_dir}")
                
                # Определить устройство для моделей (CPU или GPU)
                try:
                    from shared.config import RAG_DEVICE
                    device = RAG_DEVICE
                except ImportError:
                    device = os.getenv("RAG_DEVICE", "cpu")
                
                # Проверить доступность CUDA если указано GPU
                if device.startswith("cuda"):
                    try:
                        import torch
                        
                        # Расширенная диагностика CUDA
                        logger.info(f"🔍 Проверка доступности CUDA...")
                        logger.info(f"   PyTorch версия: {torch.__version__}")
                        logger.info(f"   CUDA доступна в PyTorch: {torch.cuda.is_available()}")
                        
                        if torch.cuda.is_available():
                            logger.info(f"   CUDA версия: {torch.version.cuda}")
                            logger.info(f"   cuDNN версия: {torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else 'недоступна'}")
                            logger.info(f"   Количество GPU: {torch.cuda.device_count()}")
                            for i in range(torch.cuda.device_count()):
                                logger.info(f"   GPU {i}: {torch.cuda.get_device_name(i)}")
                            logger.info(f"🚀 Использование GPU: {device} (доступно {torch.cuda.device_count()} устройств)")
                        else:
                            # Дополнительная диагностика
                            logger.warning(f"⚠️ CUDA запрошена ({device}), но недоступна в PyTorch.")
                            logger.warning(f"   PyTorch версия: {torch.__version__} (с поддержкой CUDA, но GPU недоступен)")
                            
                            # Проверить доступность GPU устройств в контейнере
                            nvidia_devices_found = False
                            try:
                                # Проверить наличие устройств NVIDIA в /dev
                                nvidia_devices = [f for f in os.listdir('/dev') if f.startswith('nvidia')]
                                if nvidia_devices:
                                    nvidia_devices_found = True
                                    logger.warning(f"   ✅ Устройства NVIDIA найдены в контейнере: {', '.join(nvidia_devices)}")
                                else:
                                    logger.warning(f"   ❌ Устройства NVIDIA не найдены в /dev (контейнер не имеет доступа к GPU)")
                            except Exception as e:
                                logger.warning(f"   ⚠️ Не удалось проверить /dev: {e}")
                            
                            # Проверить nvidia-smi
                            nvidia_smi_available = False
                            try:
                                import subprocess
                                result = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=5)
                                if result.returncode == 0:
                                    nvidia_smi_available = True
                                    logger.warning(f"   ✅ nvidia-smi работает в контейнере")
                                    logger.warning(f"   ⚠️ GPU обнаружен в системе, но PyTorch не видит CUDA.")
                                    logger.warning(f"   💡 Возможные причины:")
                                    logger.warning(f"      1. Версия CUDA в PyTorch ({torch.version.cuda if hasattr(torch.version, 'cuda') else 'неизвестна'}) не совпадает с версией драйвера")
                                    logger.warning(f"      2. Несовместимость версий CUDA")
                                else:
                                    logger.warning(f"   ❌ nvidia-smi недоступен или не работает")
                            except FileNotFoundError:
                                logger.warning(f"   ❌ nvidia-smi не установлен в контейнере (GPU недоступен в Docker)")
                            except (subprocess.TimeoutExpired, Exception) as e:
                                logger.warning(f"   ⚠️ Ошибка при проверке nvidia-smi: {e}")
                            
                            # Итоговые рекомендации
                            if not nvidia_devices_found and not nvidia_smi_available:
                                logger.warning(f"   🔧 РЕШЕНИЕ: Настройте доступ к GPU в Docker:")
                                logger.warning(f"      1. Установите nvidia-container-toolkit на хосте")
                                logger.warning(f"      2. Раскомментируйте секцию 'deploy' в docker-compose.yml (сервис 'bot')")
                                logger.warning(f"      3. Перезапустите Docker: sudo systemctl restart docker")
                                logger.warning(f"      4. Пересоберите контейнеры: docker-compose up -d --build")
                            elif nvidia_devices_found or nvidia_smi_available:
                                logger.warning(f"   🔧 РЕШЕНИЕ: GPU доступен в контейнере, но PyTorch его не видит.")
                                logger.warning(f"      Проверьте совместимость версий CUDA в PyTorch и драйвера.")
                            
                            logger.warning(f"   ⚠️ Используется CPU.")
                            device = "cpu"
                    except ImportError:
                        logger.warning("⚠️ PyTorch не установлен, невозможно проверить CUDA. Используется CPU.")
                        device = "cpu"
                
                # Загрузить модель с указанием пути к кэшу и устройства
                # SentenceTransformer автоматически использует кеш если модель уже загружена
                self.encoder = SentenceTransformer(model_name, cache_folder=cache_dir, device=device)
                self.dimension = self.encoder.get_sentence_embedding_dimension()
                logger.info(f"✅ Модель эмбеддингов загружена успешно (размерность: {self.dimension}, устройство: {device})")

                if not self.enable_rerank:
                    logger.info("ℹ️ Reranker отключен (RAG_ENABLE_RERANK=false)")
                    self.reranker = None
                    HAS_RERANKER = False
                else:
                    # Попробовать загрузить reranker (минимальный апгрейд качества поиска)
                    try:
                        from shared.config import RAG_RERANK_MODEL
                        rerank_model_name = RAG_RERANK_MODEL
                    except ImportError:
                        rerank_model_name = os.getenv(
                            "RAG_RERANK_MODEL",
                            "BAAI/bge-reranker-base",
                        )

                    # Проверить кеш для reranker
                    rerank_cache_name = rerank_model_name.replace("/", "--")
                    rerank_cache_path = os.path.join(cache_dir, f"models--{rerank_cache_name}")

                    if os.path.exists(rerank_cache_path):
                        logger.info(f"📥 Загрузка reranker из кэша: {rerank_model_name}")
                    else:
                        logger.info(f"📥 Загрузка reranker: {rerank_model_name}...")

                    try:
                        # Используем то же устройство что и для encoder
                        self.reranker = CrossEncoder(rerank_model_name, cache_folder=cache_dir, device=device)
                        HAS_RERANKER = True
                        logger.info(f"✅ Reranker загружен успешно: {rerank_model_name} (устройство: {device})")
                    except Exception as rerank_error:
                        logger.warning(f"⚠️ Не удалось загрузить reranker ({rerank_model_name}): {rerank_error}")
                        logger.info("   Поиск будет работать без reranker'а (только векторный поиск)")
                        self.reranker = None
                        HAS_RERANKER = False

            except Exception as e:
                logger.warning(f"⚠️ Не удалось загрузить модель эмбеддингов: {e}")
                logger.info("   Будет использоваться упрощенный поиск по ключевым словам")
                self.encoder = None
                HAS_EMBEDDINGS = False
    
    def reload_models(self) -> Dict[str, bool]:
        """
        Перезагрузить модели эмбеддингов и ранкинга из конфига в рантайме.
        
        Returns:
            dict с ключами 'embedding' и 'reranker' и значениями True/False (успех/ошибка)
        """
        global HAS_EMBEDDINGS, HAS_RERANKER
        result = {'embedding': False, 'reranker': False}
        
        # Проверить, установлены ли библиотеки (не только флаг HAS_EMBEDDINGS)
        try:
            from sentence_transformers import SentenceTransformer, CrossEncoder
            libraries_available = True
        except ImportError:
            libraries_available = False
        
        if not libraries_available:
            logger.warning("⚠️ Библиотеки sentence-transformers не установлены, перезагрузка невозможна")
            return result
        
        # Проверить, не отключен ли RAG в конфиге
        try:
            from shared.config import RAG_ENABLE
            if RAG_ENABLE is False:
                logger.warning("⚠️ RAG отключен в конфигурации (RAG_ENABLE=false), перезагрузка невозможна")
                return result
        except ImportError:
            pass  # RAG_ENABLE не указан, продолжаем
        
        try:
            # Освобождаем старые модели из памяти
            if self.encoder:
                del self.encoder
                self.encoder = None
            if self.reranker:
                del self.reranker
                self.reranker = None
            
            # Принудительная сборка мусора для освобождения памяти GPU
            import gc
            gc.collect()
            
            # Загружаем новые модели из конфига
            try:
                from shared.config import RAG_MODEL_NAME, RAG_RERANK_MODEL, RAG_DEVICE, RAG_ENABLE_RERANK
                new_model_name = RAG_MODEL_NAME
                new_rerank_model = RAG_RERANK_MODEL
                device = RAG_DEVICE
                enable_rerank = RAG_ENABLE_RERANK
                self.enable_rerank = enable_rerank
            except ImportError:
                new_model_name = os.getenv("RAG_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
                new_rerank_model = os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-base")
                device = os.getenv("RAG_DEVICE", "cpu")
                enable_rerank = os.getenv("RAG_ENABLE_RERANK", "true").lower() == "true"
                self.enable_rerank = enable_rerank
            
            # Определить путь к кэшу
            cache_dir = os.getenv("HF_HOME") or os.path.join(os.getenv("BOT_DATA_DIR", "/app/data"), "cache", "huggingface")
            os.makedirs(cache_dir, exist_ok=True)
            
            # Проверить доступность CUDA если указано GPU
            if device.startswith("cuda"):
                try:
                    import torch
                    
                    if not torch.cuda.is_available():
                        logger.warning(f"⚠️ CUDA запрошена ({device}), но недоступна при перезагрузке. Используется CPU.")
                        # Проверить nvidia-smi для диагностики
                        try:
                            import subprocess
                            result = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=5)
                            if result.returncode == 0:
                                logger.warning(f"   ⚠️ GPU обнаружен в системе, но PyTorch не видит CUDA.")
                                logger.warning(f"   💡 Проверьте: установлена ли версия PyTorch с поддержкой CUDA")
                        except:
                            pass
                        device = "cpu"
                    else:
                        logger.info(f"🚀 Перезагрузка с GPU: {device} (доступно {torch.cuda.device_count()} устройств)")
                except ImportError:
                    logger.warning("⚠️ PyTorch не установлен, невозможно проверить CUDA. Используется CPU.")
                    device = "cpu"
            
            # Загрузить новую модель эмбеддингов
            try:
                logger.info(f"🔄 Перезагрузка модели эмбеддингов: {new_model_name}")
                self.encoder = SentenceTransformer(new_model_name, cache_folder=cache_dir, device=device)
                self.dimension = self.encoder.get_sentence_embedding_dimension()
                self.model_name = new_model_name
                result['embedding'] = True
                logger.info(f"✅ Модель эмбеддингов перезагружена (размерность: {self.dimension}, устройство: {device})")
            except Exception as e:
                logger.error(f"❌ Ошибка перезагрузки модели эмбеддингов: {e}", exc_info=True)
                result['embedding'] = False
            
            # Загрузить новый reranker
            if not enable_rerank:
                logger.info("ℹ️ Reranker отключен (RAG_ENABLE_RERANK=false)")
                self.reranker = None
                HAS_RERANKER = False
                result['reranker'] = False
            else:
                try:
                    logger.info(f"🔄 Перезагрузка reranker: {new_rerank_model}")
                    self.reranker = CrossEncoder(new_rerank_model, cache_folder=cache_dir, device=device)
                    HAS_RERANKER = True
                    result['reranker'] = True
                    logger.info(f"✅ Reranker перезагружен (устройство: {device})")
                except Exception as rerank_error:
                    logger.warning(f"⚠️ Не удалось перезагрузить reranker ({new_rerank_model}): {rerank_error}")
                    self.reranker = None
                    HAS_RERANKER = False
                    result['reranker'] = False

            # Пересоздать индекс при следующем поиске (он будет пересоздан автоматически)
            self.index = None
            self.chunks = []
            self.index_by_kb.clear()
            self.chunks_by_kb.clear()
            self.bm25_index_by_kb.clear()
            self.bm25_index_all = None
            self.bm25_chunks_by_kb.clear()
            self.bm25_chunks_all = []
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при перезагрузке моделей RAG: {e}", exc_info=True)
        
        return result

    def _legacy_query_heuristics_enabled(self) -> bool:
        """Явный rollback-режим retrieval ranking heuristics."""
        try:
            from shared.config import RAG_ORCHESTRATOR_V4, RAG_LEGACY_QUERY_HEURISTICS
            return (not bool(RAG_ORCHESTRATOR_V4)) and bool(RAG_LEGACY_QUERY_HEURISTICS)
        except Exception:
            return False
    
    def _is_e5_model(self) -> bool:
        """Проверить, требует ли модель E5-style prefixes (query:/passage:)."""
        return "e5" in (self.model_name or "").lower()

    def _get_embedding(self, text: str, is_query: bool = False) -> Optional[np.ndarray]:
        """Получить эмбеддинг текста.

        Для E5-семейства моделей (multilingual-e5-*, e5-large-v2, etc.) автоматически
        добавляет обязательные префиксы: 'query: ' для запросов, 'passage: ' для документов.
        Без префиксов E5-модели работают значительно хуже.
        """
        if not HAS_EMBEDDINGS or not self.encoder:
            return None
        try:
            if self._is_e5_model():
                prefix = "query: " if is_query else "passage: "
                text = prefix + text
            if pipeline_embed_texts:
                vectors = pipeline_embed_texts([text], encoder=self.encoder)
                if not vectors or vectors[0] is None:
                    return None
                return np.array(vectors[0])
            return self.encoder.encode(text, convert_to_numpy=True)
        except Exception as e:
            logger.error(f"Ошибка создания эмбеддинга: {e}")
            return None

    def _qdrant_enabled(self) -> bool:
        return self.retrieval_backend == "qdrant" and self.qdrant_backend is not None and self.qdrant_backend.enabled

    def _qdrant_point_from_chunk(self, chunk: KnowledgeChunk) -> Optional[Dict[str, Any]]:
        if not chunk.embedding:
            return None
        try:
            vector_raw = json.loads(chunk.embedding)
            vector = np.array(vector_raw, dtype="float32")
            if vector.ndim != 1 or vector.size == 0:
                return None
            if self.qdrant_backend:
                self.qdrant_backend.ensure_collection(int(vector.shape[0]))
            payload = {
                "kb_id": int(chunk.knowledge_base_id),
                "chunk_id": int(chunk.id),
                "source_type": chunk.source_type or "",
                "source_path": chunk.source_path or "",
                "content": chunk.content or "",
                "chunk_metadata": chunk.chunk_metadata or "{}",
            }
            return {
                "id": int(chunk.id),
                "vector": vector.tolist(),
                "payload": payload,
            }
        except Exception as e:
            logger.debug("Failed to prepare qdrant point for chunk %s: %s", getattr(chunk, "id", None), e)
            return None

    def _qdrant_upsert_chunks(self, chunks: List[KnowledgeChunk]) -> int:
        if not self._qdrant_enabled() or not chunks:
            return 0
        points: List[Dict[str, Any]] = []
        for chunk in chunks:
            point = self._qdrant_point_from_chunk(chunk)
            if point:
                points.append(point)
        if not points:
            return 0
        total = 0
        batch_size = 128
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            try:
                total += self.qdrant_backend.upsert_points(batch)
            except Exception as e:
                logger.warning("Qdrant upsert batch failed: %s", e)
        return total

    def _qdrant_bootstrap(self, knowledge_base_id: Optional[int] = None) -> None:
        if not self._qdrant_enabled():
            return
        if self._qdrant_bootstrap_done and knowledge_base_id is None:
            return
        try:
            with get_session() as session:
                query = session.query(KnowledgeChunk).filter_by(is_deleted=False)
                if knowledge_base_id is not None:
                    query = query.filter_by(knowledge_base_id=knowledge_base_id)
                chunks = query.all()
            synced = self._qdrant_upsert_chunks(chunks)
            if knowledge_base_id is None:
                self._qdrant_bootstrap_done = True
            logger.info("Qdrant bootstrap sync completed: kb_id=%s synced=%s", knowledge_base_id, synced)
        except Exception as e:
            logger.warning("Qdrant bootstrap sync failed: %s", e)

    def _qdrant_dense_search(self, query_embedding: np.ndarray, knowledge_base_id: int, top_k: int) -> List[Dict[str, Any]]:
        if not self._qdrant_enabled():
            return []
        try:
            self._qdrant_bootstrap(knowledge_base_id)
            rows = self.qdrant_backend.search(
                vector=query_embedding.astype("float32").tolist(),
                limit=max(1, int(top_k)),
                kb_id=int(knowledge_base_id),
            )
            out: List[Dict[str, Any]] = []
            for row in rows:
                payload = row.payload or {}
                chunk_metadata = payload.get("chunk_metadata")
                metadata = {}
                if isinstance(chunk_metadata, str) and chunk_metadata:
                    try:
                        metadata = json.loads(chunk_metadata)
                    except Exception:
                        metadata = {}
                out.append(
                    {
                        "content": payload.get("content", ""),
                        "metadata": metadata,
                        "source_type": payload.get("source_type", ""),
                        "source_path": payload.get("source_path", ""),
                        "distance": -float(row.score),
                        "similarity": float(row.score),
                        "origin": "qdrant",
                        "chunk_id": int(payload.get("chunk_id") or row.point_id),
                    }
                )
            return out
        except Exception as e:
            logger.warning("Qdrant dense search failed, fallback to legacy: %s", e)
            return []
    
    def _load_index(self, knowledge_base_id: Optional[int] = None):
        """Загрузить индекс из базы данных (по KB или все)"""
        if not HAS_EMBEDDINGS:
            return
        
        with get_session() as session:
            if knowledge_base_id is not None:
                chunks = (
                    session.query(KnowledgeChunk)
                    .filter_by(knowledge_base_id=knowledge_base_id, is_deleted=False)
                    .all()
                )
                total_chunks = (
                    session.query(KnowledgeChunk)
                    .filter_by(knowledge_base_id=knowledge_base_id, is_deleted=False)
                    .count()
                )
            else:
                chunks = session.query(KnowledgeChunk).filter_by(is_deleted=False).all()
                total_chunks = session.query(KnowledgeChunk).filter_by(is_deleted=False).count()
            
            if not chunks:
                return

        # BM25 для всех чанков (даже без эмбеддингов)
        self.bm25_chunks_all = chunks
        self.bm25_index_all = self._build_bm25_index(chunks)

        if self._qdrant_enabled():
            all_chunks_by_kb = defaultdict(list)
            for chunk in chunks:
                all_chunks_by_kb[chunk.knowledge_base_id].append(chunk)
            self.bm25_index_by_kb.clear()
            self.bm25_chunks_by_kb.clear()
            for kb_id, kb_chunks in all_chunks_by_kb.items():
                self.bm25_chunks_by_kb[kb_id] = kb_chunks
                self.bm25_index_by_kb[kb_id] = self._build_bm25_index(kb_chunks)

            # Dense search handled by Qdrant; local FAISS indices are not used in qdrant mode.
            self.index = None
            self.chunks = []
            self.index_by_kb.clear()
            self.index_dimension_by_kb.clear()
            self.chunks_by_kb.clear()
            return

        grouped_embeddings = _group_chunk_embeddings_by_kb(chunks)
        chunks_by_kb = grouped_embeddings["chunks_by_kb"]
        all_chunks_by_kb = grouped_embeddings["all_chunks_by_kb"]
        expected_dim_by_kb = grouped_embeddings["expected_dim_by_kb"]
        dim_mismatches_by_kb = grouped_embeddings["dim_mismatches_by_kb"]
        chunks_with_embedding = int(grouped_embeddings["chunks_with_embedding"])

        if dim_mismatches_by_kb:
            summary = ", ".join(
                f"KB {kb_id}: skipped {count} (expected {expected_dim_by_kb.get(kb_id)})"
                for kb_id, count in sorted(dim_mismatches_by_kb.items())
            )
            logger.warning("Skipped chunks with dimension mismatch: %s", summary)
        
        # Логировать coverage для диагностики
        if total_chunks > 0:
            coverage_pct = (chunks_with_embedding / total_chunks) * 100
            kb_info = f"KB {knowledge_base_id}" if knowledge_base_id is not None else "all KBs"
            logger.info(
                f"Index coverage for {kb_info}: {chunks_with_embedding}/{total_chunks} chunks with embeddings ({coverage_pct:.1f}%)"
            )
            if coverage_pct < 50:
                logger.warning(f"Low embedding coverage ({coverage_pct:.1f}%) - many chunks will fall back to keyword search")
        
        # Построить индексы для каждой KB отдельно
        self.index_by_kb.clear()
        self.index_dimension_by_kb.clear()
        self.chunks_by_kb.clear()
        for kb_id, chunk_emb_pairs in chunks_by_kb.items():
            if not chunk_emb_pairs:
                continue
            
            valid_chunks = [pair[0] for pair in chunk_emb_pairs]
            embeddings = np.array([pair[1] for pair in chunk_emb_pairs]).astype('float32')
            
            # Нормализовать эмбеддинги для cosine similarity
            faiss.normalize_L2(embeddings)
            
            dimension = embeddings.shape[1]
            # Использовать IndexFlatIP (Inner Product) для cosine similarity
            index = faiss.IndexFlatIP(dimension)
            index.add(embeddings)
            
            self.index_by_kb[kb_id] = index
            self.index_dimension_by_kb[kb_id] = dimension
            self.chunks_by_kb[kb_id] = valid_chunks
            self.bm25_chunks_by_kb[kb_id] = all_chunks_by_kb.get(kb_id, [])
            self.bm25_index_by_kb[kb_id] = self._build_bm25_index(self.bm25_chunks_by_kb[kb_id])
        
        # Построить BM25 индексы для KB без эмбеддингов
        for kb_id, kb_chunks in all_chunks_by_kb.items():
            if kb_id not in self.bm25_index_by_kb:
                self.bm25_chunks_by_kb[kb_id] = kb_chunks
                self.bm25_index_by_kb[kb_id] = self._build_bm25_index(kb_chunks)

        # Для обратной совместимости: если запрошен общий индекс
        if knowledge_base_id is None and chunks_by_kb:
            target_dim = self.dimension if isinstance(getattr(self, "dimension", None), int) else None
            if target_dim is None and self.index_dimension_by_kb:
                target_dim = next(iter(self.index_dimension_by_kb.values()))
            all_chunks = []
            all_embeddings = []
            for kb_id, chunk_emb_pairs in chunks_by_kb.items():
                if target_dim is not None and self.index_dimension_by_kb.get(kb_id) != target_dim:
                    continue
                for chunk, emb in chunk_emb_pairs:
                    all_chunks.append(chunk)
                    all_embeddings.append(emb)
            
            if all_embeddings:
                self.chunks = all_chunks
                self.bm25_chunks_all = chunks
                all_embeddings = np.array(all_embeddings).astype('float32')
                faiss.normalize_L2(all_embeddings)
                self.dimension = all_embeddings.shape[1]
                self.index = faiss.IndexFlatIP(self.dimension)
                self.index.add(all_embeddings)
    

    def _tokenize(self, text: str) -> List[str]:
        import re
        tokens = re.findall(r"\w+", (text or "").lower())
        tokens = [t for t in tokens if len(t) > 2 and t not in _RU_STOP_WORDS]
        if HAS_PYMORPHY:
            tokens = [_normalize_ru(t) for t in tokens]
        return tokens

    def _build_bm25_index(self, chunks: List[KnowledgeChunk]) -> Dict:
        df: Dict[str, int] = {}
        docs_tokens: List[List[str]] = []
        total_len = 0
        for chunk in chunks:
            tokens = self._tokenize(chunk.content)
            docs_tokens.append(tokens)
            total_len += len(tokens)
            seen = set(tokens)
            for tok in seen:
                df[tok] = df.get(tok, 0) + 1
        avgdl = (total_len / len(docs_tokens)) if docs_tokens else 0.0
        return {
            "df": df,
            "docs_tokens": docs_tokens,
            "avgdl": avgdl,
            "doc_count": len(docs_tokens),
        }

    def _bm25_search(self, query: str, bm25_index: Dict, top_k: int) -> List[int]:
        import math
        q_tokens = self._tokenize(query)
        if not q_tokens or not bm25_index:
            return []
        df = bm25_index["df"]
        docs_tokens = bm25_index["docs_tokens"]
        avgdl = bm25_index["avgdl"] or 1.0
        doc_count = bm25_index["doc_count"] or 1

        k1 = 1.5
        b = 0.75

        scores = []
        for idx, tokens in enumerate(docs_tokens):
            if not tokens:
                continue
            tf = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            dl = len(tokens)
            score = 0.0
            for tok in q_tokens:
                if tok not in tf:
                    continue
                n = df.get(tok, 0)
                idf = math.log(1 + (doc_count - n + 0.5) / (n + 0.5))
                freq = tf[tok]
                denom = freq + k1 * (1 - b + b * (dl / avgdl))
                score += idf * (freq * (k1 + 1)) / (denom or 1.0)
            if score > 0:
                scores.append((idx, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in scores[:top_k]]

    def _metadata_field_search(self, query: str, chunks: List[Any], top_k: int) -> List[Dict[str, Any]]:
        """Generic metadata-first retrieval channel over stable document fields."""
        query_text = str(query or "")
        query_lower = query_text.lower()
        if not query_lower or not chunks or top_k <= 0:
            return []

        query_words = _extract_query_specificity_terms(query_text)
        if not query_words:
            return []

        is_howto = self._is_howto_query(query)
        normalized_query = _normalize_field_text_value(query_text)
        scored_chunks: List[tuple[float, Any, Dict[str, Any]]] = []
        for chunk in chunks:
            metadata: Dict[str, Any] = {}
            try:
                if chunk.chunk_metadata:
                    metadata = json.loads(chunk.chunk_metadata)
            except Exception:
                metadata = {}

            source_path_lower = (chunk.source_path or "").lower()
            doc_title_lower = (metadata.get("doc_title") or metadata.get("title") or "").lower()
            section_title_lower = (metadata.get("section_title") or "").lower()
            section_path_lower = (metadata.get("section_path") or "").lower()
            content_anchor_text = " ".join(
                [
                    str(metadata.get("section_title") or ""),
                    str(metadata.get("section_path") or ""),
                    str(chunk.content or "")[:700],
                ]
            )
            content_anchor_lower = content_anchor_text.lower()

            field_hits = 0
            score = 0.0
            for word in query_words:
                if word in doc_title_lower:
                    score += 3.0
                    field_hits += 1
                if word in section_title_lower:
                    score += 3.5
                    field_hits += 1
                if word in section_path_lower:
                    score += 2.8
                    field_hits += 1
                if word in source_path_lower:
                    score += 2.4
                    field_hits += 1
                if word in content_anchor_lower:
                    score += 1.6
                    field_hits += 1

            if query_lower in doc_title_lower:
                score += 8.0
            if query_lower in section_title_lower:
                score += 9.0
            if query_lower in section_path_lower:
                score += 7.0
            if query_lower in source_path_lower:
                score += 6.0
            if query_lower in content_anchor_lower:
                score += 5.0

            doc_metrics = _field_match_metrics(doc_title_lower, query_words, normalized_query)
            section_metrics = _field_match_metrics(section_title_lower, query_words, normalized_query)
            path_metrics = _field_match_metrics(section_path_lower, query_words, normalized_query)
            content_metrics = _field_match_metrics(content_anchor_text, query_words, normalized_query)
            score += (
                (3.2 * float(doc_metrics["coverage_ratio"]))
                + (3.8 * float(section_metrics["coverage_ratio"]))
                + (2.8 * float(path_metrics["coverage_ratio"]))
                + (2.4 * float(content_metrics["coverage_ratio"]))
                + (1.8 * float(doc_metrics["precision_ratio"]))
                + (2.2 * float(section_metrics["precision_ratio"]))
                + (1.4 * float(path_metrics["precision_ratio"]))
                + (1.0 * float(content_metrics["precision_ratio"]))
            )
            if bool(doc_metrics["exact_match"]):
                score += 3.5
            if bool(section_metrics["exact_match"]):
                score += 4.0
            if bool(path_metrics["exact_match"]):
                score += 3.0
            if bool(content_metrics["exact_match"]):
                score += 2.5

            if field_hits >= 2:
                score += min(4.0, 0.8 * field_hits)

            if is_howto:
                procedural_markers = (
                    "how to",
                    "guide",
                    "steps",
                    "instruction",
                    "instructions",
                    "setup",
                    "configure",
                    "sync",
                    "build",
                    "run",
                    "install",
                )
                if any(marker in section_title_lower for marker in procedural_markers):
                    score += 1.5
                if any(marker in doc_title_lower for marker in procedural_markers):
                    score += 1.2

            if score > 0.0:
                scored_chunks.append((score, chunk, metadata))

        scored_chunks.sort(key=lambda item: item[0], reverse=True)

        results: List[Dict[str, Any]] = []
        for score, chunk, metadata in scored_chunks[:top_k]:
            results.append(
                {
                    "content": chunk.content,
                    "metadata": metadata,
                    "source_type": chunk.source_type,
                    "source_path": chunk.source_path,
                    "distance": 1.0 / (score + 1.0),
                    "origin": "field",
                }
            )
        return results

    def _rrf_fuse(self, ranked_lists: List[List[object]], k: int = 60) -> List[object]:
        scores: Dict[object, float] = {}
        for ranked in ranked_lists:
            for rank, idx in enumerate(ranked, start=1):
                scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
        return [idx for idx, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]

    def add_knowledge_base(self, name: str, description: str = "") -> KnowledgeBase:
        """Создать новую базу знаний"""
        with _db_write_lock:
            with get_session() as session:
                kb = KnowledgeBase(name=name, description=description)
                session.add(kb)
                session.flush()  # Получить ID
                session.refresh(kb)
                return kb
    
    def get_knowledge_base(self, name_or_id) -> Optional[KnowledgeBase]:
        """Получить базу знаний по имени или ID"""
        with get_session() as session:
            if isinstance(name_or_id, int):
                return session.query(KnowledgeBase).filter_by(id=name_or_id).first()
            return session.query(KnowledgeBase).filter_by(name=name_or_id).first()
    
    def list_knowledge_bases(self) -> List[KnowledgeBase]:
        """Список всех баз знаний"""
        with get_session() as session:
            return session.query(KnowledgeBase).all()

    def _coerce_optional_int(self, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return None
        return coerced if coerced >= 0 else None

    def _coerce_optional_float(self, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, coerced))

    def _sanitize_parser_warning(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        cleaned = _SPACE_RE.sub(" ", str(value)).strip()
        if not cleaned:
            return None
        cleaned = _CREDENTIAL_URL_RE.sub(r"\1***:***@", cleaned)
        cleaned = _AUTH_HEADER_RE.sub(r"\1***", cleaned)
        cleaned = _BEARER_TOKEN_RE.sub(r"\1***", cleaned)
        cleaned = _SECRET_VALUE_RE.sub(r"\1\2***", cleaned)
        return cleaned[:500]

    def _normalize_section_path_norm(self, section_path: str) -> str:
        return _normalize_section_path_norm_value(section_path)

    def _estimate_token_count(self, content: str) -> int:
        return _estimate_token_count_value(content)

    def _build_chunk_hash_fallback(self, source_type: str, source_path: str, chunk_no: int, content: str) -> str:
        normalized_content = _SPACE_RE.sub(" ", content or "").strip()
        hash_basis = "\n".join(
            [
                str(source_type or "").strip(),
                str(source_path or "").strip(),
                str(chunk_no),
                normalized_content,
            ]
        )
        return hashlib.sha256(hash_basis.encode("utf-8", errors="ignore")).hexdigest()

    def _canonical_chunk_columns(
        self,
        *,
        content: str,
        source_type: str,
        source_path: str,
        metadata: Optional[Dict],
        metadata_json: Optional[Dict],
        chunk_columns: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        merged_meta: Dict[str, Any] = {}
        if isinstance(metadata, dict):
            merged_meta.update(metadata)
        if isinstance(metadata_json, dict):
            merged_meta.update(metadata_json)

        explicit_columns = dict(chunk_columns or {})
        section_path = str(
            explicit_columns.get("section_path_norm")
            or merged_meta.get("section_path")
            or merged_meta.get("doc_title")
            or merged_meta.get("title")
            or source_path
            or "ROOT"
        )
        block_type = str(
            explicit_columns.get("block_type")
            or merged_meta.get("block_type")
            or merged_meta.get("chunk_type")
            or merged_meta.get("chunk_kind")
            or "text"
        ).strip() or "text"
        stable_chunk_no = self._coerce_optional_int(explicit_columns.get("chunk_no", merged_meta.get("chunk_no"))) or 1

        return {
            "chunk_hash": str(
                explicit_columns.get("chunk_hash")
                or merged_meta.get("chunk_hash")
                or self._build_chunk_hash_fallback(source_type, source_path, stable_chunk_no, content)
            ).strip(),
            "chunk_no": stable_chunk_no,
            "block_type": block_type,
            "parent_chunk_id": str(
                explicit_columns.get("parent_chunk_id")
                or merged_meta.get("parent_chunk_id")
                or ""
            ).strip() or None,
            "prev_chunk_id": str(
                explicit_columns.get("prev_chunk_id")
                or merged_meta.get("prev_chunk_id")
                or ""
            ).strip() or None,
            "next_chunk_id": str(
                explicit_columns.get("next_chunk_id")
                or merged_meta.get("next_chunk_id")
                or ""
            ).strip() or None,
            "section_path_norm": str(
                explicit_columns.get("section_path_norm")
                or merged_meta.get("section_path_norm")
                or self._normalize_section_path_norm(section_path)
            ).strip(),
            "page_no": self._coerce_optional_int(explicit_columns.get("page_no", merged_meta.get("page_no", merged_meta.get("page")))),
            "char_start": self._coerce_optional_int(explicit_columns.get("char_start", merged_meta.get("char_start"))),
            "char_end": self._coerce_optional_int(explicit_columns.get("char_end", merged_meta.get("char_end"))),
            "token_count_est": self._coerce_optional_int(
                explicit_columns.get("token_count_est", merged_meta.get("token_count_est"))
            ) or self._estimate_token_count(content),
            "parser_profile": str(
                explicit_columns.get("parser_profile")
                or merged_meta.get("parser_profile")
                or f"loader:{source_type}:legacy"
            ).strip() or f"loader:{source_type}:legacy",
            "parser_confidence": self._coerce_optional_float(
                explicit_columns.get("parser_confidence", merged_meta.get("parser_confidence"))
            ),
            "parser_warning": self._sanitize_parser_warning(
                explicit_columns.get("parser_warning", merged_meta.get("parser_warning"))
            ),
        }

    def _build_canonical_chunk_payload(
        self,
        *,
        content: str,
        source_type: str,
        source_path: str,
        metadata: Optional[Dict] = None,
        metadata_json: Optional[Dict] = None,
        chunk_columns: Optional[Dict] = None,
        chunk_no: Optional[int] = None,
        chunk_title: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        effective_metadata = dict(metadata or {})
        effective_metadata_json = dict(metadata_json or effective_metadata)
        merged_meta: Dict[str, Any] = {}
        merged_meta.update(effective_metadata)
        merged_meta.update(effective_metadata_json)

        title = str(merged_meta.get("title") or chunk_title or source_path or "").strip()
        doc_title = str(merged_meta.get("doc_title") or title or source_path or "").strip()
        section_title = str(merged_meta.get("section_title") or title or doc_title or "").strip()
        section_path = str(merged_meta.get("section_path") or doc_title or source_path or "ROOT").strip()
        chunk_kind = str(
            merged_meta.get("chunk_kind")
            or merged_meta.get("chunk_type")
            or merged_meta.get("block_type")
            or "text"
        ).strip() or "text"
        stable_chunk_no = (
            self._coerce_optional_int(chunk_no)
            or self._coerce_optional_int(merged_meta.get("chunk_no"))
            or self._coerce_optional_int((chunk_columns or {}).get("chunk_no"))
            or 1
        )

        canonical_seed = dict(merged_meta)
        canonical_seed["type"] = str(merged_meta.get("type") or source_type or "unknown")
        canonical_seed["title"] = title
        canonical_seed["doc_title"] = doc_title
        canonical_seed["section_title"] = section_title
        canonical_seed["section_path"] = section_path
        canonical_seed["chunk_kind"] = chunk_kind
        canonical_seed["chunk_no"] = stable_chunk_no

        effective_chunk_columns = self._canonical_chunk_columns(
            content=content,
            source_type=source_type,
            source_path=source_path,
            metadata=canonical_seed,
            metadata_json=canonical_seed,
            chunk_columns=chunk_columns,
        )

        canonical_seed["section_path_norm"] = effective_chunk_columns["section_path_norm"]
        canonical_seed["block_type"] = effective_chunk_columns["block_type"]
        canonical_seed["chunk_hash"] = effective_chunk_columns["chunk_hash"]
        canonical_seed["chunk_no"] = effective_chunk_columns["chunk_no"]
        canonical_seed["token_count_est"] = effective_chunk_columns["token_count_est"]
        canonical_seed["parser_profile"] = effective_chunk_columns["parser_profile"]
        if effective_chunk_columns.get("page_no") is not None:
            canonical_seed["page_no"] = effective_chunk_columns["page_no"]
        if effective_chunk_columns.get("char_start") is not None:
            canonical_seed["char_start"] = effective_chunk_columns["char_start"]
        if effective_chunk_columns.get("char_end") is not None:
            canonical_seed["char_end"] = effective_chunk_columns["char_end"]
        if effective_chunk_columns.get("parser_confidence") is not None:
            canonical_seed["parser_confidence"] = effective_chunk_columns["parser_confidence"]
        if effective_chunk_columns.get("parser_warning"):
            canonical_seed["parser_warning"] = effective_chunk_columns["parser_warning"]
        if effective_chunk_columns.get("parent_chunk_id"):
            canonical_seed["parent_chunk_id"] = effective_chunk_columns["parent_chunk_id"]
        if effective_chunk_columns.get("prev_chunk_id"):
            canonical_seed["prev_chunk_id"] = effective_chunk_columns["prev_chunk_id"]
        if effective_chunk_columns.get("next_chunk_id"):
            canonical_seed["next_chunk_id"] = effective_chunk_columns["next_chunk_id"]

        effective_metadata.update(canonical_seed)
        effective_metadata_json.update(canonical_seed)
        return {
            "metadata": effective_metadata,
            "metadata_json": effective_metadata_json,
            "chunk_columns": effective_chunk_columns,
        }
    
    def add_chunk(self, knowledge_base_id: int, content: str, 
                  source_type: str = "text", source_path: str = "",
                  metadata: Optional[Dict] = None,
                  document_id: Optional[int] = None,
                  version: Optional[int] = None,
                  metadata_json: Optional[Dict] = None,
                  chunk_columns: Optional[Dict] = None,
                  chunk_no: Optional[int] = None,
                  chunk_title: Optional[str] = None,
                  is_deleted: bool = False) -> KnowledgeChunk:
        """Добавить фрагмент знания с retry логикой для обработки блокировок БД"""
        import time
        import random
        max_retries = 10  # Увеличено для длительных блокировок
        base_delay = 0.2  # Увеличено с 0.05 до 0.2 секунды
        
        # Подготовить embedding заранее, чтобы минимизировать время транзакции
        embedding = self._get_embedding(content)
        embedding_json = json.dumps(embedding.tolist()) if embedding is not None else None
        canonical_payload = self._build_canonical_chunk_payload(
            content=content,
            source_type=source_type,
            source_path=source_path,
            metadata=metadata,
            metadata_json=metadata_json,
            chunk_columns=chunk_columns,
            chunk_no=chunk_no,
            chunk_title=chunk_title,
        )
        effective_metadata = canonical_payload["metadata"]
        effective_metadata_json = canonical_payload["metadata_json"]
        canonical_columns = canonical_payload["chunk_columns"]
        
        with _db_write_lock:
            for attempt in range(max_retries):
                try:
                    with get_session() as session:
                        chunk = KnowledgeChunk(
                            knowledge_base_id=knowledge_base_id,
                            document_id=document_id,
                            version=version or 1,
                            content=content,
                            chunk_metadata=json.dumps(effective_metadata),
                            metadata_json=json.dumps(effective_metadata_json),
                            **canonical_columns,
                            embedding=embedding_json,
                            source_type=source_type,
                            source_path=source_path,
                            is_deleted=is_deleted,
                        )
                        session.add(chunk)
                        session.flush()  # Получить ID
                        session.refresh(chunk)
                    
                    # Убрать инкрементальное обновление индекса - индекс будет пересобран по запросу
                    # Это обеспечивает консистентность с cosine similarity и per-KB индексами
                    if embedding is not None and HAS_EMBEDDINGS:
                        self.index = None
                        self.chunks = []
                        self.index_by_kb.clear()
                        self.chunks_by_kb.clear()

                    # Dense index sync for qdrant backend
                    if embedding is not None and self._qdrant_enabled():
                        self._qdrant_upsert_chunks([chunk])
                    
                    return chunk
                except Exception as e:
                    if "locked" in str(e).lower() or "database is locked" in str(e):
                        if attempt < max_retries - 1:
                            # Экспоненциальный backoff с джиттером
                            delay = base_delay * (2 ** attempt)
                            jitter = delay * 0.2 * (random.random() * 2 - 1)
                            delay_with_jitter = max(0.1, delay + jitter)
                            logger.warning(
                                f"База данных заблокирована, попытка {attempt + 1}/{max_retries}, "
                                f"повтор через {delay_with_jitter:.2f}с (timeout=60s, busy_timeout=60000ms)"
                            )
                            time.sleep(delay_with_jitter)
                            continue
                        else:
                            logger.error(
                                f"Не удалось добавить чанк после {max_retries} попыток: {e} "
                                f"(timeout=60s, busy_timeout=60000ms)"
                            )
                            raise
                    else:
                        raise
    
    def add_chunks_batch(self, chunks_data: List[Dict]) -> List[KnowledgeChunk]:
        """
        Добавить несколько фрагментов знания пакетно (оптимизировано для SQLite)
        
        Использует двухфазную запись:
        1. Вставка чанков без embedding (быстро)
        2. Обновление embedding батчами (минимизирует блокировки)
        
        Args:
            chunks_data: Список словарей с данными чанков:
                {
                    'knowledge_base_id': int,
                    'content': str,
                    'source_type': str,
                    'source_path': str,
                    'metadata': dict (опционально)
                }
        
        Returns:
            Список созданных KnowledgeChunk объектов
        """
        import time
        import random
        max_retries = 10  # Увеличено для длительных блокировок
        base_delay = 0.2  # Увеличено с 0.02 до 0.2 секунды
        batch_size = 50  # Размер батча для bulk операций
        
        with _db_write_lock:
            # Подготовить все embeddings заранее (до любых транзакций)
            prepared_data = []
            for chunk_data in chunks_data:
                content = chunk_data.get('content', '')
                embedding = self._get_embedding(content)
                embedding_json = json.dumps(embedding.tolist()) if embedding is not None else None
                canonical_payload = self._build_canonical_chunk_payload(
                    content=content,
                    source_type=chunk_data.get('source_type', 'text'),
                    source_path=chunk_data.get('source_path', ''),
                    metadata=chunk_data.get('metadata'),
                    metadata_json=chunk_data.get('metadata_json'),
                    chunk_columns=chunk_data.get('chunk_columns'),
                    chunk_no=chunk_data.get('chunk_no'),
                    chunk_title=chunk_data.get('chunk_title'),
                )
                effective_metadata = canonical_payload["metadata"]
                effective_metadata_json = canonical_payload["metadata_json"]
                canonical_columns = canonical_payload["chunk_columns"]
                prepared_data.append((chunk_data, effective_metadata, effective_metadata_json, canonical_columns, embedding, embedding_json))
            
            all_chunks = []
            chunks_with_embeddings = []  # (chunk_id, embedding_json, embedding) для второй фазы
            
            # Фаза 1: Вставка чанков без embedding (быстро, минимизирует блокировки)
            for batch_start in range(0, len(prepared_data), batch_size):
                batch_data = prepared_data[batch_start:batch_start + batch_size]
                
                for attempt in range(max_retries):
                    try:
                        with get_session() as session:
                            chunks_to_add = []
                            batch_embeddings = []
                            
                            for chunk_data, effective_metadata, effective_metadata_json, canonical_columns, embedding, embedding_json in batch_data:
                                chunk = KnowledgeChunk(
                                    knowledge_base_id=chunk_data['knowledge_base_id'],
                                    document_id=chunk_data.get('document_id'),
                                    version=chunk_data.get('version') or 1,
                                    content=chunk_data.get('content', ''),
                                    chunk_metadata=json.dumps(effective_metadata),
                                    metadata_json=json.dumps(effective_metadata_json),
                                    **canonical_columns,
                                    embedding=None,  # Вставляем без embedding сначала
                                    source_type=chunk_data.get('source_type', 'text'),
                                    source_path=chunk_data.get('source_path', ''),
                                    is_deleted=bool(chunk_data.get('is_deleted', False)),
                                )
                                chunks_to_add.append(chunk)
                                if embedding_json:
                                    batch_embeddings.append((chunk, embedding_json, embedding))
                            
                            # Использовать add_all для вставки
                            session.add_all(chunks_to_add)
                            session.flush()  # Получить IDs
                            
                            # Сохранить все чанки (исправление C)
                            all_chunks.extend(chunks_to_add)
                            
                            # Сохранить для второй фазы (ID доступны после flush)
                            for chunk, emb_json, emb in batch_embeddings:
                                # ID должен быть доступен после flush
                                if hasattr(chunk, 'id') and chunk.id:
                                    chunks_with_embeddings.append((chunk.id, emb_json, emb))
                                else:
                                    logger.warning(f"Не удалось получить ID для чанка, будет пропущен embedding")
                        
                        break  # Успешно добавлено
                    except Exception as e:
                        if "locked" in str(e).lower() or "database is locked" in str(e):
                            if attempt < max_retries - 1:
                                delay = base_delay * (2 ** attempt)
                                if attempt == 0:
                                    logger.warning(f"База данных заблокирована при вставке батча {batch_start//batch_size + 1}, повторная попытка через {delay:.2f}с")
                                time.sleep(delay)
                                continue
                            else:
                                logger.error(f"Не удалось добавить батч после {max_retries} попыток: {e}")
                                raise
                        else:
                            raise
                
                # Небольшая задержка между батчами
                if batch_start + batch_size < len(prepared_data):
                    time.sleep(0.01)
            
            # Фаза 2: Обновление embedding короткими пачками с commit после каждой (исправление B)
            if chunks_with_embeddings:
                embedding_batch_size = 30  # Уменьшено для коротких транзакций
                for batch_start in range(0, len(chunks_with_embeddings), embedding_batch_size):
                    batch_embeddings = chunks_with_embeddings[batch_start:batch_start + embedding_batch_size]
                    
                    for attempt in range(max_retries):
                        try:
                            # Короткая транзакция: update + commit
                            with get_session() as session:
                                for chunk_id, embedding_json, _ in batch_embeddings:
                                    session.query(KnowledgeChunk).filter_by(id=chunk_id).update(
                                        {'embedding': embedding_json}
                                    )
                                # commit выполнится автоматически при выходе из with
                            break
                        except Exception as e:
                            if "locked" in str(e).lower() or "database is locked" in str(e):
                                if attempt < max_retries - 1:
                                    # Экспоненциальный backoff с джиттером
                                    delay = base_delay * (2 ** attempt)
                                    jitter = delay * 0.2 * (random.random() * 2 - 1)
                                    delay_with_jitter = max(0.1, delay + jitter)
                                    logger.warning(
                                        f"База данных заблокирована при обновлении embedding батча, "
                                        f"попытка {attempt + 1}/{max_retries}, повтор через {delay_with_jitter:.2f}с"
                                    )
                                    time.sleep(delay_with_jitter)
                                    continue
                                else:
                                    logger.error(
                                        f"Не удалось обновить embedding батча после {max_retries} попыток: {e} "
                                        f"(timeout=60s, busy_timeout=60000ms)"
                                    )
                                    # Не прерываем процесс, просто логируем ошибку
                                    break
                            else:
                                raise

            if self._qdrant_enabled() and chunks_with_embeddings:
                try:
                    ids = [int(chunk_id) for chunk_id, _, _ in chunks_with_embeddings if chunk_id]
                    if ids:
                        with get_session() as session:
                            chunk_rows = session.query(KnowledgeChunk).filter(KnowledgeChunk.id.in_(ids)).all()
                        self._qdrant_upsert_chunks(chunk_rows)
                except Exception as e:
                    logger.warning("Qdrant sync after batch insert failed: %s", e)
            
            # Исправление D: отключить инкрементальное обновление индекса после батча
            # Индекс будет пересобран по запросу через _load_index()
            # Это снижает число обращений к БД в момент записи
            # После успешного большого импорта просто сбрасываем индекс
            if HAS_EMBEDDINGS and chunks_with_embeddings:
                self.index = None
                self.chunks = []
                # Очистить индексы по KB
                self.index_by_kb.clear()
                self.chunks_by_kb.clear()
                self.bm25_index_by_kb.clear()
                self.bm25_index_all = None
                self.bm25_chunks_by_kb.clear()
                self.bm25_chunks_all = []
                # Индекс будет пересобран при следующем поиске через _load_index()
        
        return all_chunks
    
    def search(self, query: str, knowledge_base_id: Optional[int] = None, 
               top_k: int = 5) -> List[Dict]:
        """Поиск в базе знаний (dense + keyword с возможным rerank)."""
        import re
        legacy_query_heuristics_enabled = self._legacy_query_heuristics_enabled()
        # Токенизация запроса для использования в how-to бустах
        query_words = re.findall(r'\w+', query.lower())
        query_lower = query.lower()

        def compute_source_boost(candidate: Dict) -> float:
            if not legacy_query_heuristics_enabled:
                return 0.0
            tokens = [t for t in query_words if len(t) >= 3]
            if not tokens:
                return 0.0
            source_path = (candidate.get("source_path") or "").lower()
            metadata = candidate.get("metadata") or {}
            doc_title = (metadata.get("doc_title") or metadata.get("title") or "").lower()
            section_path = (metadata.get("section_path") or "").lower()
            boost = 0.0
            if source_path and any(t in source_path for t in tokens):
                boost += 0.08
            if doc_title and any(t in doc_title for t in tokens):
                boost += 0.06
            if section_path and any(t in section_path for t in tokens):
                boost += 0.04
            if source_path and query_lower in source_path:
                boost += 0.1
            return min(boost, 0.25)
        
        # Если эмбеддинги недоступны – используем только упрощённый поиск
        if not HAS_EMBEDDINGS or not self.encoder:
            return self._simple_search(query, knowledge_base_id, top_k)
        
        # Загрузить кэши поиска если не загружены
        if knowledge_base_id is not None:
            if knowledge_base_id not in self.bm25_index_by_kb or knowledge_base_id not in self.bm25_chunks_by_kb:
                self._load_index(knowledge_base_id)
        else:
            if not self.bm25_index_all or not self.bm25_chunks_all:
                self._load_index(None)
        
        # Векторный поиск
        query_embedding = self._get_embedding(query, is_query=True)
        if query_embedding is None:
            return self._simple_search(query, knowledge_base_id, top_k)

        # В generalized режиме how-to определяется только как hint, но не меняет ranking.
        is_howto_query = self._is_howto_query(query)
        use_legacy_howto_ranking = legacy_query_heuristics_enabled and is_howto_query

        bm25_chunks = self.bm25_chunks_by_kb.get(knowledge_base_id) if knowledge_base_id is not None else self.bm25_chunks_all
        bm25_chunks = bm25_chunks or []
        total_docs = max(1, len(bm25_chunks))

        # Dense/BM25 каналы используют явные бюджеты; более широкие окна включаются конфигом, а не скрытой веткой.
        dense_candidate_k = min(self.dense_candidate_budget, total_docs)

        dense_candidates: List[Dict] = []
        if self._qdrant_enabled() and knowledge_base_id is not None:
            dense_candidates = self._qdrant_dense_search(
                query_embedding=query_embedding,
                knowledge_base_id=int(knowledge_base_id),
                top_k=dense_candidate_k,
            )
        else:
            # Определить, использовать ли индекс по KB или общий
            if knowledge_base_id is not None:
                if knowledge_base_id not in self.index_by_kb:
                    return self._simple_search(query, knowledge_base_id, top_k)
                index = self.index_by_kb[knowledge_base_id]
                chunks = self.chunks_by_kb[knowledge_base_id]
            else:
                if self.index is None or len(self.chunks) == 0:
                    return self._simple_search(query, knowledge_base_id, top_k)
                index = self.index
                chunks = self.chunks

            if len(chunks) == 0:
                return []

            # Нормализовать query embedding для cosine similarity
            query_embedding_norm = query_embedding.reshape(1, -1).astype('float32')
            query_dim = int(query_embedding_norm.shape[1])
            if knowledge_base_id is not None:
                expected_dim = self.index_dimension_by_kb.get(knowledge_base_id)
                if expected_dim is not None and query_dim != expected_dim:
                    logger.warning(
                        "Dense search dimension mismatch for KB %s: query=%s index=%s; fallback to keyword search",
                        knowledge_base_id,
                        query_dim,
                        expected_dim,
                    )
                    return self._simple_search(query, knowledge_base_id, top_k)
            elif self.dimension and query_dim != int(self.dimension):
                logger.warning(
                    "Dense search dimension mismatch for global index: query=%s index=%s; fallback to keyword search",
                    query_dim,
                    self.dimension,
                )
                return self._simple_search(query, knowledge_base_id, top_k)
            faiss.normalize_L2(query_embedding_norm)
            dense_candidate_k = min(dense_candidate_k, len(chunks))
            scores, indices = index.search(query_embedding_norm, dense_candidate_k)

            for i, idx in enumerate(indices[0]):
                if idx < len(chunks):
                    chunk = chunks[idx]
                    metadata = json.loads(chunk.chunk_metadata) if chunk.chunk_metadata else {}
                    similarity = float(scores[0][i])
                    if use_legacy_howto_ranking:
                        chunk_kind = metadata.get("chunk_kind", "text")
                        if chunk_kind in ("code", "code_file", "list"):
                            similarity *= 1.5
                        section_path = (metadata.get("section_path") or "").lower()
                        if section_path and any(word in section_path for word in query_words):
                            similarity *= 1.2
                    distance = -similarity
                    dense_candidates.append(
                        {
                            "content": chunk.content,
                            "metadata": metadata,
                            "source_type": chunk.source_type,
                            "source_path": chunk.source_path,
                            "distance": distance,
                            "similarity": similarity,
                            "origin": "dense",
                            "chunk_idx": idx,
                        }
                    )

        # BM25 поиск как второй канал (на всех чанках)
        bm25_index = self.bm25_index_by_kb.get(knowledge_base_id) if knowledge_base_id is not None else self.bm25_index_all
        if bm25_index is None:
            bm25_index = self._build_bm25_index(bm25_chunks)
            if knowledge_base_id is not None:
                self.bm25_index_by_kb[knowledge_base_id] = bm25_index
            else:
                self.bm25_index_all = bm25_index
        bm25_ranked = self._bm25_search(query, bm25_index or {}, self.bm25_candidate_budget)
        bm25_candidates: List[Dict] = []
        bm25_ranked_keys: List[tuple] = []
        for rank, idx in enumerate(bm25_ranked, start=1):
            if idx < len(bm25_chunks):
                chunk = bm25_chunks[idx]
                metadata = json.loads(chunk.chunk_metadata) if chunk.chunk_metadata else {}
                key = (chunk.source_path or "", (chunk.content or "")[:200])
                bm25_ranked_keys.append(key)
                bm25_candidates.append(
                    {
                        "content": chunk.content,
                        "metadata": metadata,
                        "source_type": chunk.source_type,
                        "source_path": chunk.source_path,
                        "distance": 1.0 / (rank + 1),
                        "origin": "bm25",
                        "key": key,
                    }
                )

        field_candidates = self._metadata_field_search(
            query,
            bm25_chunks,
            min(self.bm25_candidate_budget, total_docs),
        )
        field_ranked_keys = [(c.get("source_path") or "", (c.get("content") or "")[:200]) for c in field_candidates]

        # RRF fusion on stable keys
        ranked_lists = []
        dense_ranked_keys = [(c.get("source_path") or "", (c.get("content") or "")[:200]) for c in dense_candidates]
        if dense_ranked_keys:
            ranked_lists.append(dense_ranked_keys)
        if bm25_ranked_keys:
            ranked_lists.append(bm25_ranked_keys)
        if field_ranked_keys:
            ranked_lists.append(field_ranked_keys)
        fused_keys = self._rrf_fuse(ranked_lists) if ranked_lists else []

        # Объединяем кандидатов и убираем дубли по (source_path, content)
        merged: List[Dict] = []
        seen = set()
        candidate_map = {
            (c.get("source_path") or "", (c.get("content") or "")[:200]): c
            for c in dense_candidates + bm25_candidates + field_candidates
        }
        for key in fused_keys:
            cand = candidate_map.get(key)
            if not cand:
                continue
            if key in seen:
                continue
            seen.add(key)
            cand["rank_score"] = 1.0 / (len(merged) + 1)
            merged.append(cand)

        if not merged:
            return []

        if not use_legacy_howto_ranking:
            merged = _order_candidates_by_family_support(
                merged,
                channel_candidates={
                    "dense": dense_candidates,
                    "bm25": bm25_candidates,
                    "field": field_candidates,
                },
            )
            merged = _order_candidates_by_query_field_specificity(merged, query=query)
            merged = _order_candidates_by_canonicality(merged, query=query)

        # Если есть reranker – пересчитываем релевантность и берём top_k по score
        if self.enable_rerank and HAS_RERANKER and self.reranker is not None:
            try:
                rerank_window = min(len(merged), max(top_k, self.rerank_top_n))
                rerank_candidates = merged[:rerank_window]
                pairs = [[query, c.get("content", "")] for c in rerank_candidates]
                scores = self.reranker.predict(pairs)

                scored = []
                for cand, score in zip(rerank_candidates, scores):
                    boost = compute_source_boost(cand)
                    scored.append((cand, float(score), boost, float(score) + boost))
                scored.sort(
                    key=lambda item: (
                        item[3],
                        int(bool(item[0].get("_query_field_exact_match"))),
                        int(bool(item[0].get("_query_field_best_exact"))),
                        float(item[0].get("_query_field_best_coverage", 0.0)),
                        float(item[0].get("_query_field_best_precision", 0.0)),
                        float(item[0].get("_query_field_specificity_score", 0.0)),
                        float(item[0].get("_canonicality_net_score", 0.0)),
                        -float(item[0].get("_contamination_penalty", 0.0)),
                        -(int(item[0].get("_family_rank", 999999))),
                        int(item[0].get("_family_channel_count", 0)),
                        int(item[0].get("_family_candidate_count", 0)),
                        float(item[0].get("_family_support_rrf", 0.0)),
                    ),
                    reverse=True,
                )
                top = scored[: top_k]

                logger.debug(
                    "Reranker применен: обработано %d кандидатов из %d fused, выбрано top-%d",
                    len(rerank_candidates),
                    len(merged),
                    len(top),
                )
                if top:
                    logger.debug(
                        "Лучший rerank_score: %.4f, худший: %.4f",
                        float(top[0][1]),
                        float(top[-1][1]),
                    )

                results = []
                for cand, score, boost, _ in top:
                    results.append(
                        {
                            "content": cand.get("content", ""),
                            "metadata": cand.get("metadata") or {},
                            "source_type": cand.get("source_type"),
                            "source_path": cand.get("source_path"),
                            "distance": float(cand.get("distance", 0.0)),
                            "rerank_score": float(score),
                            "source_boost": float(boost),
                            "origin": cand.get("origin"),
                        }
                    )
                return results
            except Exception as e:
                logger.warning("⚠️ Ошибка работы reranker, продолжаю без него: %s", e)
                import traceback
                logger.debug("Traceback reranker: %s", traceback.format_exc())
                # fallthrough к сортировке merged (dense + keyword)
        
        # Если reranker недоступен — legacy how-to ранжир остается только в rollback-режиме.
        if use_legacy_howto_ranking:
            # Для how-to: is_code_or_list (code/list выше) → origin_priority (bm25 выше) → distance
            def sort_key_howto(c):
                metadata = c.get("metadata") or {}
                chunk_kind = metadata.get("chunk_kind", "text")
                is_code_or_list = 0 if chunk_kind in ("code", "code_file", "list") else 1  # code/list = 0 (выше)
                origin_priority = 0 if c.get("origin") == "bm25" else 1  # bm25 = 0 (выше), dense = 1
                distance = float(c.get("distance", float("inf")))
                boost = compute_source_boost(c)
                return (is_code_or_list, origin_priority, distance - boost)
            merged_sorted = sorted(merged, key=sort_key_howto)[: top_k]
        else:
            # В generalized path без reranker сохраняем explicit fusion order:
            # повторная сортировка по distance ломает hybrid retrieval и выталкивает
            # metadata/BM25 rescue обратно за dense candidates.
            merged_sorted = merged[: top_k]
        
        results = []
        for cand in merged_sorted:
            boost = compute_source_boost(cand)
            results.append(
                {
                    "content": cand.get("content", ""),
                    "metadata": cand.get("metadata") or {},
                    "source_type": cand.get("source_type"),
                    "source_path": cand.get("source_path"),
                    "distance": float(cand.get("distance", 0.0)),
                    "source_boost": float(boost),
                    "origin": cand.get("origin", "dense"),
                }
            )
        return results
    
    def _is_howto_query(self, query: str) -> bool:
        """Определить, является ли запрос запросом типа 'how-to' (инструкция/процедура)."""
        import re
        query_lower = query.lower()
        
        # Ключевые слова, указывающие на how-to запрос
        howto_keywords = [
            'how to', 'howto', 'how do', 'how can', 'how should',
            'initialize', 'init', 'setup', 'set up', 'install', 'configure',
            'create', 'build', 'compile', 'sync', 'sync and build',
            'run', 'execute', 'start', 'begin', 'get started',
            'tutorial', 'guide', 'steps', 'procedure', 'process',
            'command', 'example', 'demo'
        ]
        
        # Проверка на наличие how-to ключевых слов
        for keyword in howto_keywords:
            if keyword in query_lower:
                return True
        
        # Проверка на паттерны типа "как сделать", "как создать" и т.д.
        russian_howto_patterns = [
            r'как\s+(сделать|создать|настроить|установить|запустить|начать)',
            r'инструкция',
            r'руководство',
            r'шаги',
        ]
        for pattern in russian_howto_patterns:
            if re.search(pattern, query_lower):
                return True
        
        return False
    
    def _simple_search(self, query: str, knowledge_base_id: Optional[int] = None, 
                      top_k: int = 5) -> List[Dict]:
        """Упрощенный поиск по ключевым словам"""
        import re
        import json
        legacy_query_heuristics_enabled = self._legacy_query_heuristics_enabled()
        # Улучшенная токенизация: разбиваем по пробелам и специальным символам
        query_lower = query.lower()
        # Разбиваем по пробелам, амперсандам, дефисам и другим разделителям
        query_words = re.findall(r'\w+', query_lower)
        
        # В generalized режиме how-to не должен менять SQL prefilter или ranking.
        is_howto = legacy_query_heuristics_enabled and self._is_howto_query(query)
        
        # Сильные токены для how-to запросов (команды, флаги, ключевые слова)
        strong_tokens = ['repo', '--depth', '--reference', 'mkdir', 'cd', 'git', 'init', 'sync', 
                        'build', 'compile', 'install', 'docker', 'npm', 'yarn', 'pip', 'apt', 'yum']
        
        with get_session() as session:
            # Для how-to запросов с сильными токенами: предварительный SQL-фильтр
            if is_howto:
                # Найти сильные токены в запросе
                found_strong_tokens = [token for token in strong_tokens if token in query_lower]
                
                if found_strong_tokens:
                    # Построить SQL-фильтр: content LIKE '%token%' OR content LIKE '%token%' ...
                    filters = []
                    for token in found_strong_tokens:
                        # Ищем токен как отдельное слово или как часть команды
                        filters.append(KnowledgeChunk.content.like(f'%{token}%'))
                    
                    # Базовый запрос с фильтром по KB
                    query_obj = session.query(KnowledgeChunk)
                    if knowledge_base_id is not None:
                        query_obj = query_obj.filter_by(knowledge_base_id=knowledge_base_id, is_deleted=False)
                    else:
                        query_obj = query_obj.filter_by(is_deleted=False)
                    
                    # Применить SQL-фильтр по сильным токенам
                    chunks = query_obj.filter(or_(*filters)).all()
                    logger.debug(f"Pre-filtered {len(chunks)} chunks using strong tokens: {found_strong_tokens}")
                else:
                    # Нет сильных токенов - загружаем все (как раньше)
                    if knowledge_base_id is not None:
                        chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id, is_deleted=False).all()
                    else:
                        chunks = session.query(KnowledgeChunk).filter_by(is_deleted=False).all()
            else:
                # Для обычных запросов - загружаем все (как раньше)
                if knowledge_base_id is not None:
                    chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id, is_deleted=False).all()
                else:
                    chunks = session.query(KnowledgeChunk).filter_by(is_deleted=False).all()
        
        scored_chunks = []
        for chunk in chunks:
            content_lower = chunk.content.lower()
            # Также проверяем source_path для лучшего поиска по именам файлов
            source_path_lower = (chunk.source_path or "").lower()
            
            # Извлекаем метаданные для поиска по заголовкам
            metadata = {}
            try:
                if chunk.chunk_metadata:
                    metadata = json.loads(chunk.chunk_metadata)
            except:
                pass
            
            # Поиск в заголовке и section_title (важно для поиска по заголовкам документов)
            title_lower = (metadata.get("title") or "").lower()
            section_title_lower = (metadata.get("section_title") or "").lower()
            section_path_lower = (metadata.get("section_path") or "").lower()
            chunk_kind = metadata.get("chunk_kind", "text")
            
            # Подсчет совпадений в контенте
            content_score = sum(1 for word in query_words if word in content_lower)
            
            # Бонус за совпадение в заголовке (очень важно)
            title_score = sum(2 for word in query_words if word in title_lower)
            
            # Бонус за совпадение в section_title
            section_score = sum(1.5 for word in query_words if word in section_title_lower)
            
            # Бонус за совпадение в section_path
            section_path_score = sum(1.5 for word in query_words if word in section_path_lower)
            
            # Бонус за совпадение в имени файла/пути
            path_score = sum(1 for word in query_words if word in source_path_lower)
            
            # Для how-to запросов: буст для code/list чанков
            chunk_kind_boost = 0
            if is_howto and chunk_kind in ("code", "code_file", "list"):
                chunk_kind_boost = 3
            
            # Поиск командных строк в контенте (для how-to)
            command_score = 0
            if is_howto:
                command_pattern = r'(^|\n)(repo|git|mkdir|cd|python|docker|npm|yarn|pip|apt|yum)\b'
                if re.search(command_pattern, chunk.content, re.IGNORECASE):
                    command_score = 2
            
            # Также проверяем точное совпадение фразы (для запросов типа "Initialize repository and sync code")
            phrase_in_content = query_lower in content_lower
            phrase_in_title = query_lower in title_lower
            phrase_in_section = query_lower in section_title_lower
            phrase_in_section_path = query_lower in section_path_lower
            
            total_score = (
                content_score + 
                title_score * 3 +  # Заголовок очень важен
                section_score * 2 +
                section_path_score * 2.5 +  # section_path важен для навигации
                path_score * 2 +
                chunk_kind_boost +
                command_score +
                (10 if phrase_in_title else 0) +  # Большой бонус за точное совпадение в заголовке
                (8 if phrase_in_section_path else 0) +
                (5 if phrase_in_section else 0) +
                (3 if phrase_in_content else 0)
            )
            
            if total_score > 0:
                scored_chunks.append((total_score, chunk))
        
        scored_chunks.sort(reverse=True, key=lambda x: x[0])
        
        results = []
        for score, chunk in scored_chunks[:top_k]:
            results.append({
                'content': chunk.content,
                'metadata': json.loads(chunk.chunk_metadata) if chunk.chunk_metadata else {},
                'source_type': chunk.source_type,
                'source_path': chunk.source_path,
                'distance': 1.0 / (score + 1)  # Обратное расстояние
            })
        
        return results
    
    def delete_knowledge_base(self, knowledge_base_id: int) -> bool:
        """Удалить базу знаний, все её фрагменты и журнал загрузок"""
        with _db_write_lock:
            with get_session() as session:
                # Удалить все фрагменты
                chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
                for chunk in chunks:
                    session.delete(chunk)
                
                # Удалить все записи из журнала загрузок для этой базы знаний
                logs = session.query(KnowledgeImportLog).filter_by(knowledge_base_id=knowledge_base_id).all()
                for log in logs:
                    session.delete(log)
                
                # Удалить саму базу знаний
                kb = session.query(KnowledgeBase).filter_by(id=knowledge_base_id).first()
                if kb:
                    session.delete(kb)
                    session.flush()
                
                if not kb:
                    return False
            
            # Пересоздать индекс (вне сессии)
            self.chunks = []
            self.index = None
            self.index_by_kb.clear()
            self.chunks_by_kb.clear()
            self.bm25_index_by_kb.clear()
            self.bm25_index_all = None
            self.bm25_chunks_by_kb.clear()
            self.bm25_chunks_all = []
            self._load_index()
            if self._qdrant_enabled():
                try:
                    self.qdrant_backend.delete_kb(int(knowledge_base_id))
                except Exception as e:
                    logger.warning("Qdrant delete_kb failed for kb=%s: %s", knowledge_base_id, e)
            return True
    
    def clear_knowledge_base(self, knowledge_base_id: int) -> bool:
        """Очистить базу знаний от всех фрагментов и журнала загрузок"""
        with _db_write_lock:
            with get_session() as session:
                # Удалить все фрагменты
                chunks = session.query(KnowledgeChunk).filter_by(knowledge_base_id=knowledge_base_id).all()
                for chunk in chunks:
                    session.delete(chunk)
                
                # Удалить все записи из журнала загрузок для этой базы знаний
                logs = session.query(KnowledgeImportLog).filter_by(knowledge_base_id=knowledge_base_id).all()
                for log in logs:
                    session.delete(log)
                session.flush()
            
            # Пересоздать индекс (вне сессии)
            self.chunks = []
            self.index = None
            self.index_by_kb.clear()
            self.chunks_by_kb.clear()
            self.bm25_index_by_kb.clear()
            self.bm25_index_all = None
            self.bm25_chunks_by_kb.clear()
            self.bm25_chunks_all = []
            self._load_index()
            if self._qdrant_enabled():
                try:
                    self.qdrant_backend.delete_kb(int(knowledge_base_id))
                except Exception as e:
                    logger.warning("Qdrant clear_kb failed for kb=%s: %s", knowledge_base_id, e)
            return True

    def delete_chunks_by_source_exact(
        self,
        knowledge_base_id: int,
        source_type: str,
        source_path: str,
        soft_delete: bool = True,
    ) -> int:
        """
        Удалить фрагменты знаний для конкретного источника в рамках БЗ.

        Используется при обновлении документов: новая версия заменяет старые данные.
        """
        if not source_path:
            return 0

        with _db_write_lock:
            with get_session() as session:
                q = (
                    session.query(KnowledgeChunk)
                    .filter_by(
                        knowledge_base_id=knowledge_base_id,
                        source_type=source_type,
                        source_path=source_path,
                    )
                )
                chunks = q.all()
                deleted = 0
                for chunk in chunks:
                    if soft_delete:
                        chunk.is_deleted = True
                    else:
                        session.delete(chunk)
                    deleted += 1
                session.flush()

            if deleted:
                # Полностью пересоздать индекс, чтобы он соответствовал текущему состоянию БД
                self.chunks = []
                self.index = None
                # Удалить индекс для этой KB
                if knowledge_base_id in self.index_by_kb:
                    del self.index_by_kb[knowledge_base_id]
                if knowledge_base_id in self.chunks_by_kb:
                    del self.chunks_by_kb[knowledge_base_id]
                if knowledge_base_id in self.bm25_index_by_kb:
                    del self.bm25_index_by_kb[knowledge_base_id]
                if knowledge_base_id in self.bm25_chunks_by_kb:
                    del self.bm25_chunks_by_kb[knowledge_base_id]
                # Пересоздать индекс для этой KB
                self._load_index(knowledge_base_id)
                if self._qdrant_enabled():
                    try:
                        self.qdrant_backend.delete_by_filter(
                            kb_id=int(knowledge_base_id),
                            source_type=source_type,
                            source_path=source_path,
                        )
                    except Exception as e:
                        logger.warning(
                            "Qdrant delete_by_filter failed: kb_id=%s source_type=%s source_path=%s error=%s",
                            knowledge_base_id,
                            source_type,
                            source_path,
                            e,
                        )

            return deleted

    def delete_chunks_by_source_prefix(
        self,
        knowledge_base_id: int,
        source_type: str,
        source_prefix: str,
        soft_delete: bool = True,
    ) -> int:
        """
        Удалить фрагменты знаний по префиксу источника (например, все страницы одной вики).
        
        Это используется wiki-скрепером для пересборки вики без очистки всей базы знаний.
        """
        if not source_prefix:
            return 0

        with _db_write_lock:
            with get_session() as session:
                # Найти все фрагменты в указанной базе знаний и с нужным типом источника
                query = (
                    session.query(KnowledgeChunk)
                    .filter_by(knowledge_base_id=knowledge_base_id, source_type=source_type)
                )
                chunks = query.all()
                deleted = 0

                deleted_source_paths = set()
                for chunk in chunks:
                    if chunk.source_path and chunk.source_path.startswith(source_prefix):
                        deleted_source_paths.add(chunk.source_path)
                        if soft_delete:
                            chunk.is_deleted = True
                        else:
                            session.delete(chunk)
                        deleted += 1
                session.flush()

            if deleted:
                # Полностью пересоздать индекс, чтобы он соответствовал текущему состоянию БД
                self.chunks = []
                self.index = None
                # Удалить индекс для этой KB
                if knowledge_base_id in self.index_by_kb:
                    del self.index_by_kb[knowledge_base_id]
                if knowledge_base_id in self.chunks_by_kb:
                    del self.chunks_by_kb[knowledge_base_id]
                if knowledge_base_id in self.bm25_index_by_kb:
                    del self.bm25_index_by_kb[knowledge_base_id]
                if knowledge_base_id in self.bm25_chunks_by_kb:
                    del self.bm25_chunks_by_kb[knowledge_base_id]
                # Пересоздать индекс для этой KB
                self._load_index(knowledge_base_id)
                if self._qdrant_enabled():
                    for src in deleted_source_paths:
                        try:
                            self.qdrant_backend.delete_by_filter(
                                kb_id=int(knowledge_base_id),
                                source_type=source_type,
                                source_path=src,
                            )
                        except Exception as e:
                            logger.warning(
                                "Qdrant delete_by_prefix item failed: kb_id=%s source_type=%s source_path=%s error=%s",
                                knowledge_base_id,
                                source_type,
                                src,
                                e,
                            )

            return deleted


# Глобальный экземпляр RAG системы
rag_system = RAGSystem()

