from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Callable, Dict, Any, List, Optional, Tuple
from datetime import datetime
from uuid import uuid4
from time import perf_counter
import json
import logging
import os
import re

from backend.api.deps import get_db_dep, require_api_key
from backend.schemas.rag import (
    RAGQuery,
    RAGAnswer,
    RAGSource,
    RAGSummaryQuery,
    RAGSummaryAnswer,
    RAGDiagnosticsResponse,
    RAGDiagnosticsCandidate,
    RAGEvalRunRequest,
    RAGEvalRunResponse,
    RAGEvalStatusResponse,
    RAGEvalResultRow,
)

# Используем существующую RAG-систему и AI-менеджер из основного проекта.
from shared.rag_system import (  # type: ignore
    rag_system,
    build_query_focused_excerpt,
    describe_context_chunk,
    merge_multi_query_candidates,
    _order_candidates_by_query_field_specificity,
    _order_candidates_by_canonicality,
)
from shared.ai_providers import ai_manager  # type: ignore
from shared.utils import create_prompt_with_language, normalize_wiki_url_for_display  # type: ignore
from shared.rag_safety import (  # type: ignore
    assess_query_security,
    build_security_refusal_message,
    find_poisoned_context_rows,
    strip_unknown_citations,
    strip_untrusted_urls,
    sanitize_commands_in_answer,
)
from shared.logging_config import logger  # type: ignore
from shared.database import (
    KnowledgeBase,
    KnowledgeChunk,
    RetrievalQueryLog,
    RetrievalCandidateLog,
)  # type: ignore
from shared.kb_settings import normalize_kb_settings  # type: ignore
from backend.services.rag_eval_service import rag_eval_service


router = APIRouter(prefix="/rag", tags=["rag"])

_CONTEXT_TRACE_SELECTED_KEY = "_diag_context_selected"
_CONTEXT_TRACE_RANK_KEY = "_diag_context_rank"
_CONTEXT_TRACE_REASON_KEY = "_diag_context_reason"
_CONTEXT_TRACE_ANCHOR_RANK_KEY = "_diag_context_anchor_rank"
_FAMILY_TRACE_KEY = "_diag_family_key"
_FAMILY_TRACE_RANK_KEY = "_diag_family_rank"
_CANONICALITY_TRACE_SCORE_KEY = "_diag_canonicality_score"
_CONTAMINATION_TRACE_PENALTY_KEY = "_diag_contamination_penalty"
_CANONICALITY_TRACE_REASON_KEY = "_diag_canonicality_reason"
_CONTAMINATION_TRACE_REASON_KEY = "_diag_contamination_reason"
_PROVIDER_ERROR_PREFIXES = (
    "Ошибка подключения к ",
    "Ошибка при обращении к ",
)


def _safe_json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return None


def _safe_json_loads(value: Optional[str]) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    try:
        obj = json.loads(value)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _metric_to_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.6f}"
    except Exception:
        text = str(value).strip()
        return text[:32] if text else None


def _metric_to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        coerced = int(value)
    except Exception:
        return None
    return coerced if coerced > 0 else None


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "y"}:
            return True
        if token in {"0", "false", "no", "n"}:
            return False
    return None


def _normalize_trace_token(value: Any, *, default: str = "unknown", max_len: int = 32) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return default
    return token[:max_len]


def _derive_distance_score(trace: Dict[str, Any]) -> Optional[float]:
    distance = _metric_to_float(trace.get("distance"))
    if distance is None:
        return None
    return -distance


def _derive_fusion_score_value(trace: Dict[str, Any]) -> Optional[float]:
    for key in ("fusion_score", "rank_score"):
        value = _metric_to_float(trace.get(key))
        if value is not None:
            return value
    rerank_score = _metric_to_float(trace.get("rerank_score"))
    if rerank_score is not None:
        return rerank_score
    return _derive_distance_score(trace)


def _derive_rerank_delta_value(trace: Dict[str, Any]) -> Optional[float]:
    explicit_delta = _metric_to_float(trace.get("rerank_delta"))
    if explicit_delta is not None:
        return explicit_delta
    rerank_score = _metric_to_float(trace.get("rerank_score"))
    base_score = _derive_distance_score(trace)
    if rerank_score is None or base_score is None:
        return None
    return rerank_score - base_score


def _collect_grounded_url_allowlist(results: Optional[List[Dict[str, Any]]]) -> List[str]:
    allowlist: List[str] = []
    seen: set[str] = set()

    def add_url(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        value = candidate.strip()
        if not value.startswith(("http://", "https://")):
            return
        display_value = normalize_wiki_url_for_display(value)
        for url in (value, display_value):
            normalized = (url or "").strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            allowlist.append(normalized)

    for row in results or []:
        if not isinstance(row, dict):
            continue
        add_url(row.get("source_path"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for key in ("source_path", "url", "source_url", "wiki_url", "wiki_root"):
            add_url(metadata.get(key))
    return allowlist


def _build_rag_sources(rows: List[dict]) -> List[RAGSource]:
    sources: list[RAGSource] = []
    seen_source_keys: set = set()
    for chunk in rows:
        try:
            metadata = chunk.get("metadata") or {}
            _page = metadata.get("page_no", metadata.get("page"))
            _page = _page if isinstance(_page, int) else None
            _section = (
                metadata.get("section_title")
                or metadata.get("section_path")
                or chunk.get("title")
                or None
            )
            _path = chunk.get("source_path") or metadata.get("source_path") or ""
            _type = chunk.get("source_type") or metadata.get("source_type") or ""
            _key = (_path.lower(), _page)
            if _key in seen_source_keys:
                continue
            seen_source_keys.add(_key)
            sources.append(
                RAGSource(
                    source_path=_path,
                    source_type=_type,
                    score=float(chunk.get("rerank_score") or chunk.get("distance", 0.0)),
                    page_number=_page,
                    section_title=_section,
                )
            )
        except (ValueError, TypeError) as e:
            logger.warning("Error processing source chunk: %s", e, exc_info=True)
            continue
    return sources


def _classify_provider_transport_error(answer: str) -> str:
    text = str(answer or "").strip()
    if not text:
        return ""
    if text == "Провайдер ИИ не найден":
        return "provider_unavailable"
    if any(text.startswith(prefix) for prefix in _PROVIDER_ERROR_PREFIXES):
        lowered = text.lower()
        if "timed out" in lowered or "timeout" in lowered or "таймаут" in lowered:
            return "timeout"
        if re.search(r":\s*503\b", lowered):
            return "provider_unavailable"
        return "provider_transport_error"
    return ""


def _format_fallback_chunk_label(row: Dict[str, Any]) -> str:
    info = describe_context_chunk(row)
    parts: List[str] = []
    doc_title = str(info.get("doc_title") or "").strip()
    source_path = str(info.get("source_path") or "").strip()
    page_no = info.get("page_no")
    section_title = str(info.get("section_title") or "").strip()
    if doc_title:
        parts.append(doc_title)
    elif source_path:
        parts.append(source_path.rsplit("/", 1)[-1] or source_path)
    if isinstance(page_no, int):
        parts.append(f"стр. {page_no}")
    if section_title and section_title not in parts:
        parts.append(section_title)
    return " | ".join(parts) if parts else "Релевантный фрагмент"


def _build_extractive_fallback_answer(
    query: str,
    rows: List[Dict[str, Any]],
    *,
    failure_reason: str,
) -> str:
    if failure_reason == "timeout":
        intro = "Модель ответа сейчас недоступна или отвечает слишком долго."
    else:
        intro = "Модель ответа сейчас временно недоступна."

    rendered_items: List[str] = []
    seen_excerpts: set[str] = set()
    for row in rows or []:
        info = describe_context_chunk(row)
        excerpt = build_query_focused_excerpt(
            query,
            str(info.get("content") or row.get("content") or ""),
            max_length=420,
            chunk_kind=str(info.get("chunk_kind") or "text"),
        ).strip()
        if not excerpt:
            continue
        normalized_excerpt = excerpt.lower()
        if normalized_excerpt in seen_excerpts:
            continue
        seen_excerpts.add(normalized_excerpt)
        label = _format_fallback_chunk_label(row)
        rendered_items.append(f"{len(rendered_items) + 1}. {label}\n{excerpt}")
        if len(rendered_items) >= 3:
            break

    if not rendered_items:
        return f"{intro} Не удалось сформировать ответ модели, но релевантные источники найдены ниже."

    return (
        f"{intro} Показываю наиболее релевантные фрагменты из базы знаний.\n\n"
        + "\n\n".join(rendered_items)
    )


def _postprocess_grounded_answer(
    answer: str,
    *,
    context_text: str,
    grounded_url_allowlist: List[str],
) -> str:
    sanitized = strip_unknown_citations(answer, context_text)
    sanitized = strip_untrusted_urls(
        sanitized,
        context_text,
        allowed_urls=grounded_url_allowlist,
    )
    sanitized = sanitize_commands_in_answer(sanitized, context_text)
    return sanitized


def _normalize_query_text(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "")).strip()


def _extract_query_hints(query: str) -> Dict[str, Any]:
    q = _normalize_query_text(query).lower()
    point_numbers = re.findall(r"(?:пункт[а-я]*|section|clause)\s+(\d+)", q)
    definition_term = ""
    prefixes = (
        "как в документе определяется ",
        "как определяется ",
        "что такое ",
        "что называется ",
        "что включает ",
        "what is ",
        "what does ",
        "define ",
    )
    for prefix in prefixes:
        if q.startswith(prefix):
            definition_term = q[len(prefix):].strip(" ?!.,:;")
            break
    if not definition_term:
        m = re.search(r"определ[а-я]*\s+([а-яёa-z0-9\-\s]{3,})$", q)
        if m:
            definition_term = (m.group(1) or "").strip(" ?!.,:;")
    if definition_term:
        definition_term = re.sub(r"^(в документе|в стратегии)\s+", "", definition_term).strip()
        definition_term = re.sub(r"\s+", " ", definition_term).strip(" ?!.,:;")
    stop_words = {
        "about",
        "define",
        "please",
        "такое",
        "what",
        "when",
        "where",
        "which",
        "with",
        "подскажи",
        "расскажи",
        "нужно",
        "можно",
        "что",
        "как",
        "когда",
        "кто",
        "какой",
        "какая",
        "какие",
        "каких",
        "какому",
        "какими",
        "где",
        "для",
        "чего",
        "чему",
        "почему",
        "зачем",
        "каков",
        "какова",
        "каковы",
        "пункте",
        "документе",
        "стратегии",
        "искусственного",
        "интеллекта",
        "развития",
    }
    fact_terms: List[str] = []
    for token in re.findall(r"[а-яёa-z0-9]{3,}", q):
        if token in stop_words or token in fact_terms:
            continue
        fact_terms.append(token)
    year_tokens = re.findall(r"\b(20\d{2})\b", q)
    key_phrase_candidates = (
        "ежегодный объем",
        "объем услуг",
        "совокупной мощности",
        "мощности суперкомпьютеров",
        "совокупный прирост ввп",
        "прирост ввп",
        "на какой период",
        "период рассчитана",
        "федеральные законы",
        "правовой основе",
        "механизмы реализации",
        "основные принципы",
        "корректировке стратегии",
        "принимает решение",
    )
    key_phrases = [phrase for phrase in key_phrase_candidates if phrase in q]
    metric_query = any(
        marker in q
        for marker in (
            "целевой",
            "показател",
            "объем",
            "мощност",
            "ввп",
            "прирост",
            "период",
            "как часто",
            "федеральные законы",
            "механизмы реализации",
            "основные принципы",
        )
    )
    strict_fact_terms = [t for t in fact_terms if len(t) >= 5][:8]
    prefer_numeric = metric_query and any(
        marker in q for marker in ("целевой", "показател", "объем", "мощност", "ввп", "прирост", "сколько")
    )
    return {
        "original_query": q,
        "point_numbers": point_numbers,
        "definition_term": definition_term,
        "fact_terms": fact_terms[:10],
        "year_tokens": year_tokens,
        "metric_query": metric_query,
        "prefer_numeric": prefer_numeric,
        "strict_fact_terms": strict_fact_terms,
        "key_phrases": key_phrases[:5],
    }


def _infer_query_intent(query: str, hints: Optional[Dict[str, Any]] = None) -> str:
    q = _normalize_query_text(query).lower()
    if not q:
        return "GENERAL"
    hints = hints or {}
    definition_term = str(hints.get("definition_term") or "").strip().lower()
    point_numbers = [str(v).strip() for v in (hints.get("point_numbers") or []) if str(v).strip()]
    year_tokens = [str(v).strip() for v in (hints.get("year_tokens") or []) if str(v).strip()]
    metric_query = bool(hints.get("metric_query"))
    fact_terms = [str(v).strip().lower() for v in (hints.get("fact_terms") or []) if str(v).strip()]

    if definition_term:
        return "DEFINITION"
    if metric_query or point_numbers or year_tokens:
        return "FACTOID"

    procedural_terms = [
        "how to",
        "как ",
        "build",
        "run",
        "install",
        "setup",
        "compile",
        "unit test",
        "unittest",
        "tests",
        "guide",
        "steps",
    ]
    trouble_terms = [
        "error",
        "errors",
        "fail",
        "failed",
        "not working",
        "issue",
        "issues",
        "stacktrace",
        "fix",
        "debug",
        "white screen",
        "troubleshoot",
    ]
    definition_terms = [
        "что такое",
        "как определяется",
        "как в документе определяется",
        "что называется",
        "что включает",
        "definition",
        "defined as",
        "what is",
        "define ",
    ]
    factoid_terms = [
        "кто",
        "какой",
        "какие",
        "какая",
        "сколько",
        "как часто",
        "when",
        "who",
        "what target",
        "who decides",
        "how often",
    ]

    if any(term in q for term in procedural_terms):
        return "HOWTO"
    if any(term in q for term in trouble_terms):
        return "TROUBLE"
    if any(term in q for term in definition_terms):
        return "DEFINITION"
    if fact_terms or any(term in q for term in factoid_terms):
        return "FACTOID"
    return "GENERAL"


def _build_controlled_query_variants(
    query: str,
    hints: Optional[Dict[str, Any]],
    *,
    max_variants: int = 3,
) -> List[Dict[str, str]]:
    original = _normalize_query_text(query)
    if not original:
        return []

    normalized_original = original.lower()
    variants: List[Dict[str, str]] = [{"query": original, "mode": "original", "reason": "original"}]
    seen = {normalized_original}
    hints = hints or {}
    is_cyrillic = bool(re.search(r"[а-яё]", normalized_original))
    definition_term = _normalize_query_text(hints.get("definition_term") or "").lower()
    point_numbers = [str(v).strip() for v in (hints.get("point_numbers") or []) if str(v).strip()]
    key_phrases = [_normalize_query_text(v).lower() for v in (hints.get("key_phrases") or []) if str(v).strip()]
    strict_fact_terms = [_normalize_query_text(v).lower() for v in (hints.get("strict_fact_terms") or []) if str(v).strip()]
    fact_terms = [_normalize_query_text(v).lower() for v in (hints.get("fact_terms") or []) if str(v).strip()]
    year_tokens = [str(v).strip() for v in (hints.get("year_tokens") or []) if str(v).strip()]

    def add_variant(candidate: str, mode: str, reason: str) -> None:
        text = _normalize_query_text(candidate)
        if len(text) < 3:
            return
        normalized = text.lower()
        if normalized in seen:
            return
        variants.append({"query": text, "mode": mode, "reason": reason})
        seen.add(normalized)

    if definition_term:
        add_variant(
            f"определение {definition_term}" if is_cyrillic else f"{definition_term} definition",
            "definition_focus",
            "definition_term",
        )

    focus_terms: List[str] = []
    for token in [*key_phrases, *strict_fact_terms, *fact_terms]:
        if token and token not in focus_terms:
            focus_terms.append(token)

    if point_numbers:
        point_no = point_numbers[0]
        point_prefix = f"пункт {point_no}" if is_cyrillic else f"section {point_no}"
        add_variant(" ".join([point_prefix, *focus_terms[:3]]).strip(), "point_focus", "point_number")

    fact_focus_terms = [*key_phrases[:2], *strict_fact_terms[:3], *year_tokens[:1]]
    if fact_focus_terms:
        add_variant(" ".join(fact_focus_terms), "fact_focus", "content_terms")
    elif len(fact_terms) >= 2 and len(normalized_original.split()) >= 4:
        add_variant(" ".join(fact_terms[:5]), "keyword_focus", "content_terms")

    return variants[: max(1, int(max_variants))]


def _should_enable_controlled_rewrites(query: str, hints: Optional[Dict[str, Any]]) -> bool:
    normalized = _normalize_query_text(query).lower()
    if not normalized:
        return False
    token_count = len(re.findall(r"[а-яёa-z0-9]+", normalized))
    if token_count < 2:
        return False

    hints = hints or {}
    definition_term = _normalize_query_text(hints.get("definition_term") or "")
    point_numbers = [str(v).strip() for v in (hints.get("point_numbers") or []) if str(v).strip()]
    metric_query = bool(hints.get("metric_query"))
    key_phrases = [str(v).strip() for v in (hints.get("key_phrases") or []) if str(v).strip()]
    conversational_prefixes = (
        "подскажи",
        "расскажи",
        "где найти",
        "как найти",
        "how do i",
        "where can i",
        "can i",
    )

    if definition_term:
        return token_count <= 7
    if point_numbers:
        return token_count <= 8
    if metric_query:
        return token_count <= 6 and len(key_phrases) <= 1
    if any(normalized.startswith(prefix) for prefix in conversational_prefixes):
        return token_count <= 10
    return token_count <= 7


def _run_controlled_multi_query_search(
    *,
    query: str,
    knowledge_base_id: Optional[int],
    top_k: int,
    hints: Optional[Dict[str, Any]],
    enable_rewrites: bool,
) -> tuple[List[dict], Dict[str, Any]]:
    variants = (
        _build_controlled_query_variants(query, hints, max_variants=3)
        if enable_rewrites
        else [{"query": _normalize_query_text(query), "mode": "original", "reason": "original"}]
    )
    if not variants:
        variants = [{"query": _normalize_query_text(query), "mode": "original", "reason": "original"}]

    batches: List[List[Dict[str, Any]]] = []
    rewrite_queries: List[str] = []
    rewrite_modes: List[str] = []
    rewrite_reasons: List[str] = []

    for variant in variants:
        variant_query = variant.get("query") or _normalize_query_text(query)
        batch = rag_system.search(
            query=variant_query,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
        ) or []
        annotated_batch: List[Dict[str, Any]] = []
        for rank, item in enumerate(batch, start=1):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["query_variant_mode"] = variant.get("mode") or "original"
            row["query_variant_query"] = variant_query
            row["query_variant_reason"] = variant.get("reason") or "original"
            row["query_variant_rank"] = rank
            annotated_batch.append(row)
        batches.append(annotated_batch)
        if (variant.get("mode") or "original") != "original":
            rewrite_queries.append(variant_query)
            rewrite_modes.append(str(variant.get("mode") or ""))
            rewrite_reasons.append(str(variant.get("reason") or ""))

    merged_results = merge_multi_query_candidates(batches) if len(batches) > 1 else (batches[0] if batches else [])
    trace = {
        "query_rewrite_enabled": bool(enable_rewrites),
        "multi_query_applied": len(variants) > 1,
        "query_variant_count": len(variants),
        "query_rewrite_variants": rewrite_queries,
        "query_rewrite_modes": rewrite_modes,
        "query_rewrite_reasons": rewrite_reasons,
    }
    return merged_results, trace


def _extract_hint_mode(hints: Optional[Dict[str, Any]], key: str) -> Optional[str]:
    if not isinstance(hints, dict):
        return None
    raw_value = hints.get(key)
    if raw_value is None:
        return None
    token = str(raw_value).strip().lower()
    return token or None


def _derive_degraded_mode(
    *,
    backend_name: Optional[str],
    candidates: Optional[List[Dict[str, Any]]],
) -> tuple[bool, Optional[str]]:
    backend = (backend_name or "").strip().lower()
    if backend != "qdrant":
        return False, None

    origins: List[str] = []
    for row in candidates or []:
        if not isinstance(row, dict):
            continue
        origin = (row.get("origin") or "").strip().lower()
        if origin:
            origins.append(origin)

    if not origins:
        return False, None
    if any(origin == "qdrant" for origin in origins):
        return False, None
    if any(origin in {"bm25", "keyword", "legacy"} for origin in origins):
        return True, "qdrant_unavailable_or_empty"
    return True, "dense_channel_unavailable"


def _build_context_trace_tokens(row: dict) -> List[str]:
    info = describe_context_chunk(row)
    tokens: List[str] = []
    seen: set[str] = set()

    def add_token(value: str) -> None:
        token = str(value or "").strip()
        if not token or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    identity = str(info.get("identity") or "").strip()
    if identity:
        add_token(f"identity:{identity}")
    chunk_hash = str(info.get("chunk_hash") or "").strip()
    if chunk_hash:
        add_token(f"chunk_hash:{chunk_hash}")

    source_path = str(info.get("source_path") or "").strip().lower()
    scope_key = str(info.get("scope_key") or "").strip().lower()
    chunk_no = _coerce_positive_int(info.get("chunk_no"))
    page_no = _coerce_positive_int(info.get("page_no"))

    if source_path and chunk_no is not None:
        add_token(f"path_chunk:{source_path}:{chunk_no}")
    if source_path and scope_key and chunk_no is not None:
        add_token(f"path_scope_chunk:{source_path}:{scope_key}:{chunk_no}")
    if source_path and page_no is not None and chunk_no is not None:
        add_token(f"path_page_chunk:{source_path}:{page_no}:{chunk_no}")

    normalized_text = " ".join(str(row.get("content") or "").split())[:240]
    if source_path and normalized_text:
        add_token(f"path_text:{source_path}:{normalized_text}")
    elif normalized_text:
        add_token(f"text:{normalized_text}")
    return tokens


def _load_doc_chunks_for_context(
    db: Session,
    doc_id: str,
    *,
    kb_id: Optional[int] = None,
) -> List[dict]:
    if not doc_id:
        return []
    if not hasattr(db, "query"):
        return []
    query = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.source_path == doc_id)
        .filter(KnowledgeChunk.is_deleted == False)
    )
    if kb_id is not None:
        query = query.filter(KnowledgeChunk.knowledge_base_id == kb_id)
    rows = query.order_by(KnowledgeChunk.id.asc()).all()
    chunks: List[dict] = []
    for row in rows:
        try:
            meta = row.chunk_metadata
            meta_obj = json.loads(meta) if meta else {}
        except Exception:
            meta_obj = {}
        chunks.append(
            {
                "id": row.id,
                "content": row.content or "",
                "metadata": meta_obj,
                "source_type": row.source_type,
                "source_path": row.source_path,
            }
        )

    def sort_key(item: dict) -> tuple[int, int]:
        info = describe_context_chunk(item)
        chunk_no = info.get("chunk_no")
        return (
            int(chunk_no) if isinstance(chunk_no, int) else int(item.get("id") or 0),
            int(item.get("id") or 0),
        )

    return sorted(chunks, key=sort_key)


def _build_context_source_id(row: dict) -> str:
    info = describe_context_chunk(row)
    source_path = info.get("source_path") or ""
    if source_path and ".keep" not in source_path.lower():
        if "::" in source_path:
            base_id = source_path.split("::")[-1]
        elif "/" in source_path:
            base_id = source_path.split("/")[-1]
        else:
            base_id = source_path
        base_id = base_id.rsplit(".", 1)[0] if "." in base_id else base_id
        suffix = info.get("id")
        if suffix is None:
            suffix = info.get("chunk_no")
        return f"{base_id}_{suffix}" if suffix is not None else base_id
    return str(info.get("doc_title") or "source").replace(" ", "_").lower()[:50]


def _build_context_block(
    *,
    query: str,
    row: dict,
    base_context_length: int,
    full_multiplier: int,
    enable_citations: bool,
) -> Optional[str]:
    try:
        info = describe_context_chunk(row)
        chunk_kind = str(info.get("chunk_kind") or "text")
        code_lang = ((row.get("metadata") if isinstance(row.get("metadata"), dict) else {}) or {}).get("code_lang") or ""
        content = row.get("content") or ""
        if chunk_kind in ("code", "code_file"):
            max_length = base_context_length * 3
        elif chunk_kind in ("full_page", "full_doc"):
            max_length = base_context_length * max(1, full_multiplier)
        elif chunk_kind == "list":
            max_length = base_context_length * 2
        else:
            max_length = base_context_length

        content_preview = build_query_focused_excerpt(
            query,
            content,
            max_length=max_length,
            chunk_kind=chunk_kind,
        )
        if not content_preview:
            return None

        context_block_parts = []
        if enable_citations:
            context_block_parts.append(f"SOURCE_ID: {_build_context_source_id(row)}")
        if info.get("doc_title"):
            context_block_parts.append(f"DOC: {info['doc_title']}")
        if info.get("section_path"):
            context_block_parts.append(f"SECTION: {info['section_path']}")
        elif info.get("section_title"):
            context_block_parts.append(f"SECTION: {info['section_title']}")
        if chunk_kind:
            context_block_parts.append(f"TYPE: {chunk_kind}")
        if code_lang:
            context_block_parts.append(f"LANG: {code_lang}")
        context_block_parts.append("CONTENT:")
        context_block_parts.append(content_preview)
        context_block_parts.append("---")
        return "\n".join(context_block_parts)
    except Exception as e:  # noqa: BLE001
        logger.warning("Error building context block: %s", e, exc_info=True)
        return None


def _resolve_doc_chunk_anchor_index(anchor: dict, doc_chunks: List[dict]) -> Optional[int]:
    if not doc_chunks:
        return None
    anchor_info = describe_context_chunk(anchor)
    anchor_text = (anchor.get("content") or "").strip()
    for idx, candidate in enumerate(doc_chunks):
        candidate_info = describe_context_chunk(candidate)
        if anchor_info.get("id") is not None and anchor_info.get("id") == candidate_info.get("id"):
            return idx
    for idx, candidate in enumerate(doc_chunks):
        candidate_info = describe_context_chunk(candidate)
        if (
            anchor_info.get("chunk_no") is not None
            and anchor_info.get("chunk_no") == candidate_info.get("chunk_no")
            and anchor_info.get("scope_key") == candidate_info.get("scope_key")
        ):
            return idx
    for idx, candidate in enumerate(doc_chunks):
        candidate_info = describe_context_chunk(candidate)
        if anchor_info.get("chunk_hash") and anchor_info.get("chunk_hash") == candidate_info.get("chunk_hash"):
            return idx
    if not anchor_text:
        return None
    normalized_anchor = " ".join(anchor_text.split())[:240]
    for idx, candidate in enumerate(doc_chunks):
        candidate_text = " ".join(str(candidate.get("content") or "").split())[:240]
        if candidate_text and candidate_text == normalized_anchor:
            return idx
    return None


def _generalized_field_match_score(
    query: str,
    hints: Optional[Dict[str, Any]],
    result: Dict[str, Any],
) -> float:
    hints = hints or {}
    meta = result.get("metadata") or {}
    source_path = _normalize_query_text(result.get("source_path") or "").lower()
    doc_title = _normalize_query_text(meta.get("doc_title") or meta.get("title") or "").lower()
    section_title = _normalize_query_text(meta.get("section_title") or "").lower()
    section_path = _normalize_query_text(meta.get("section_path") or "").lower()
    field_text = " | ".join(part for part in (source_path, doc_title, section_title, section_path) if part).strip()
    if not field_text:
        return 0.0

    focus_terms: List[str] = []
    for token in [*(hints.get("key_phrases") or []), *(hints.get("strict_fact_terms") or []), *(hints.get("fact_terms") or [])]:
        normalized = _normalize_query_text(token).lower()
        if normalized and normalized not in focus_terms:
            focus_terms.append(normalized)
    if not focus_terms:
        focus_terms = [token for token in re.findall(r"[a-zа-яё0-9]{3,}", _normalize_query_text(query).lower()) if token not in {"how", "what", "when"}]
    if not focus_terms:
        return 0.0

    overlap_terms = [term for term in focus_terms if term in field_text]
    if not overlap_terms:
        return 0.0

    score = min(0.9, 0.3 * len(overlap_terms))
    if len(overlap_terms) >= 2 and len(focus_terms) >= 2:
        score += 0.6
    compound_markers = {"sync", "build", "mirror", "master", "repo"}
    if len(overlap_terms) >= 2 and compound_markers.intersection(overlap_terms):
        score += 0.4
    return score


def _expand_anchor_evidence_rows(anchor: dict, doc_chunks: List[dict]) -> List[dict]:
    if not doc_chunks:
        return [anchor]
    anchor_info = describe_context_chunk(anchor)
    anchor_family_key = str(anchor.get("_family_key") or "").strip()
    anchor_family_rank = _coerce_positive_int(anchor.get("_family_rank"))
    if anchor_info.get("chunk_kind") in {"code_file", "full_doc"}:
        return [anchor]
    anchor_index = _resolve_doc_chunk_anchor_index(anchor, doc_chunks)
    if anchor_index is None:
        return [anchor]

    selected: List[tuple[int, dict, str]] = [(anchor_index, doc_chunks[anchor_index], "primary")]
    seen_indexes = {anchor_index}

    def maybe_add(index: int, reason: str) -> None:
        if index < 0 or index >= len(doc_chunks) or index in seen_indexes:
            return
        candidate = doc_chunks[index]
        candidate_info = describe_context_chunk(candidate)
        same_scope = candidate_info.get("scope_key") == anchor_info.get("scope_key")
        same_page = (
            anchor_info.get("page_no") is not None
            and anchor_info.get("page_no") == candidate_info.get("page_no")
        )
        same_doc = candidate_info.get("doc_key") == anchor_info.get("doc_key")
        neighbor_distance = None
        if anchor_info.get("chunk_no") is not None and candidate_info.get("chunk_no") is not None:
            neighbor_distance = abs(int(anchor_info["chunk_no"]) - int(candidate_info["chunk_no"]))
        if same_scope or same_page or (same_doc and neighbor_distance == 1):
            selected.append((index, candidate, reason))
            seen_indexes.add(index)

    maybe_add(anchor_index - 1, "adjacent_prev")
    maybe_add(anchor_index + 1, "adjacent_next")

    same_scope_candidates: List[tuple[int, int, dict]] = []
    for index, candidate in enumerate(doc_chunks):
        if index in seen_indexes:
            continue
        candidate_info = describe_context_chunk(candidate)
        if candidate_info.get("scope_key") != anchor_info.get("scope_key"):
            continue
        if anchor_info.get("chunk_no") is not None and candidate_info.get("chunk_no") is not None:
            distance = abs(int(anchor_info["chunk_no"]) - int(candidate_info["chunk_no"]))
            order = int(candidate_info["chunk_no"])
        else:
            distance = abs(anchor_index - index)
            order = index
        same_scope_candidates.append((distance, order, candidate))
    same_scope_candidates.sort(key=lambda item: (item[0], item[1]))
    for _distance, _order, candidate in same_scope_candidates[:2]:
        candidate_index = doc_chunks.index(candidate)
        if candidate_index in seen_indexes:
            continue
        selected.append((candidate_index, candidate, "section_scope"))
        seen_indexes.add(candidate_index)

    selected.sort(key=lambda item: (0 if item[2] == "primary" else 1, abs(item[0] - anchor_index), item[0]))
    expanded_rows: List[dict] = []
    for _index, candidate, reason in selected:
        row = dict(candidate)
        row["_context_reason"] = reason
        if anchor_family_key:
            row["_family_key"] = anchor_family_key
        if anchor_family_rank is not None:
            row["_family_rank"] = anchor_family_rank
        expanded_rows.append(row)
    return expanded_rows


def _context_family_key(row: dict) -> str:
    info = describe_context_chunk(row)
    doc_key = str(info.get("doc_key") or info.get("source_path") or "").strip().lower()
    scope_key = str(info.get("scope_key") or "").strip().lower()
    identity = str(info.get("identity") or "").strip().lower()
    if doc_key and scope_key.startswith(("section:", "page:")):
        return f"{doc_key}::{scope_key}"
    if doc_key:
        return doc_key
    return identity


def _order_rows_by_family_cohesion(ranked_results: List[dict]) -> List[dict]:
    if not ranked_results:
        return []

    family_buckets: Dict[str, List[tuple[int, dict]]] = {}
    family_best: Dict[str, float] = {}
    family_total: Dict[str, float] = {}
    family_first_index: Dict[str, int] = {}
    family_contiguous: Dict[str, bool] = {}

    for index, row in enumerate(ranked_results or []):
        family_key = _context_family_key(row)
        if not family_key:
            family_key = f"row:{index}"
        family_buckets.setdefault(family_key, []).append((index, row))
        score = float(row.get("rank_score") or row.get("rerank_score") or 0.0)
        family_best[family_key] = max(family_best.get(family_key, float("-inf")), score)
        family_total[family_key] = family_total.get(family_key, 0.0) + max(0.0, score)
        family_first_index.setdefault(family_key, index)

    for family_key, entries in family_buckets.items():
        chunk_numbers = []
        for _index, row in entries:
            chunk_no = describe_context_chunk(row).get("chunk_no")
            if isinstance(chunk_no, int):
                chunk_numbers.append(chunk_no)
        contiguous = False
        if len(chunk_numbers) >= 2:
            chunk_numbers = sorted(set(chunk_numbers))
            contiguous = any((right - left) <= 1 for left, right in zip(chunk_numbers, chunk_numbers[1:]))
        family_contiguous[family_key] = contiguous

    top_best = max(family_best.values(), default=float("-inf"))
    ordered_family_keys = sorted(
        family_buckets.keys(),
        key=lambda key: (
            1 if family_best.get(key, float("-inf")) >= (top_best - 0.12) else 0,
            family_best.get(key, float("-inf"))
            + min(0.12, 0.04 * max(0, len(family_buckets.get(key, [])) - 1))
            + (0.05 if family_contiguous.get(key, False) else 0.0)
            + min(0.08, max(0.0, family_total.get(key, 0.0) - family_best.get(key, 0.0)) * 0.05),
            family_best.get(key, float("-inf")),
            family_total.get(key, float("-inf")),
            -family_first_index.get(key, 0),
        ),
        reverse=True,
    )

    ordered_rows: List[dict] = []
    for family_rank, family_key in enumerate(ordered_family_keys, start=1):
        family_entries = sorted(
            family_buckets.get(family_key, []),
            key=lambda item: (
                float(item[1].get("rank_score") or item[1].get("rerank_score") or 0.0),
                -item[0],
            ),
            reverse=True,
        )
        for _index, row in family_entries:
            decorated = dict(row)
            decorated["_family_rank"] = family_rank
            decorated["_family_key"] = family_key
            ordered_rows.append(decorated)
    return ordered_rows


def _select_evidence_pack_rows(
    *,
    ranked_results: List[dict],
    load_doc_chunks: Callable[[str], List[dict]],
    anchor_limit: int,
    context_limit: int,
) -> List[dict]:
    if not ranked_results:
        return []
    pack_rows: List[dict] = []
    seen_identities: set[str] = set()
    seen_match_tokens: set[str] = set()
    seen_anchor_scopes: set[tuple[str, str]] = set()
    seen_anchor_docs: set[str] = set()
    anchor_entries: List[tuple[int, dict, List[dict]]] = []
    anchor_rank_by_scope: Dict[tuple[str, str], int] = {}
    anchor_rank_by_doc: Dict[str, int] = {}

    def add_candidate(candidate: dict, *, reason: str, anchor_rank: Optional[int] = None) -> bool:
        info = describe_context_chunk(candidate)
        identity = str(info.get("identity") or "")
        if not identity:
            return False
        match_tokens = _build_context_trace_tokens(candidate)
        if identity in seen_identities:
            return False
        if match_tokens and any(token in seen_match_tokens for token in match_tokens):
            return False
        row = dict(candidate)
        row["_context_reason"] = row.get("_context_reason") or reason
        if anchor_rank is not None:
            row["_context_anchor_rank"] = anchor_rank
        seen_identities.add(identity)
        seen_match_tokens.update(match_tokens)
        pack_rows.append(row)
        return True

    for anchor in ranked_results:
        anchor_info = describe_context_chunk(anchor)
        anchor_scope = (str(anchor_info.get("doc_key") or ""), str(anchor_info.get("scope_key") or ""))
        if anchor_scope in seen_anchor_scopes:
            continue
        if len(seen_anchor_scopes) >= max(1, anchor_limit):
            break
        seen_anchor_scopes.add(anchor_scope)
        seen_anchor_docs.add(str(anchor_info.get("doc_key") or ""))
        doc_id = str(anchor.get("doc_id") or anchor_info.get("source_path") or "").strip()
        doc_chunks = load_doc_chunks(doc_id) if doc_id else []
        expanded_rows = _expand_anchor_evidence_rows(anchor, doc_chunks)
        if not expanded_rows:
            expanded_rows = [anchor]
        anchor_rank = len(anchor_entries) + 1
        anchor_entries.append((anchor_rank, anchor, expanded_rows))
        anchor_rank_by_scope[anchor_scope] = anchor_rank
        doc_key = str(anchor_info.get("doc_key") or "")
        if doc_key and doc_key not in anchor_rank_by_doc:
            anchor_rank_by_doc[doc_key] = anchor_rank

    for anchor_rank, _anchor, expanded_rows in anchor_entries:
        if add_candidate(expanded_rows[0], reason="primary", anchor_rank=anchor_rank):
            if len(pack_rows) >= context_limit:
                return pack_rows

    for anchor_rank, _anchor, expanded_rows in anchor_entries:
        for candidate in expanded_rows[1:]:
            if add_candidate(
                candidate,
                reason=str(candidate.get("_context_reason") or "supporting"),
                anchor_rank=anchor_rank,
            ):
                if len(pack_rows) >= context_limit:
                    return pack_rows

    for candidate in ranked_results:
        candidate_info = describe_context_chunk(candidate)
        candidate_scope = (str(candidate_info.get("doc_key") or ""), str(candidate_info.get("scope_key") or ""))
        if (
            candidate_scope not in seen_anchor_scopes
            and str(candidate_info.get("doc_key") or "") not in seen_anchor_docs
        ):
            continue
        anchor_rank = (
            anchor_rank_by_scope.get(candidate_scope)
            or anchor_rank_by_doc.get(str(candidate_info.get("doc_key") or ""))
        )
        if add_candidate(candidate, reason="fallback_rank", anchor_rank=anchor_rank):
            if len(pack_rows) >= context_limit:
                break
    return pack_rows


def _select_provider_fallback_rows(
    *,
    query: str,
    ranked_results: List[dict],
    load_doc_chunks: Callable[[str], List[dict]],
    anchor_limit: int,
    context_limit: int,
) -> List[dict]:
    if not ranked_results:
        return []
    query_lower = _normalize_query_text(query).lower()
    query_tokens = set(re.findall(r"[a-zа-яё0-9]{3,}", query_lower))
    howto_like = any(token in query_lower for token in ("how to", "как ", "build", "sync", "install", "setup", "configure", "run"))
    procedural_markers = ("how to", "guide", "steps", "initialize", "setup", "install", "configure", "prepare", "sync", "build", "run")
    troubleshooting_markers = (
        "issue",
        "issues",
        "error",
        "errors",
        "fix",
        "fixes",
        "patch",
        "workaround",
        "debug",
        "troubleshoot",
        "regeneration",
        "failed",
        "failure",
    )

    def fallback_priority(row: dict) -> tuple[float, float]:
        info = describe_context_chunk(row)
        meta = row.get("metadata") or {}
        doc_title = _normalize_query_text(meta.get("doc_title") or meta.get("title") or "").lower()
        section_title = _normalize_query_text(meta.get("section_title") or "").lower()
        section_path = _normalize_query_text(meta.get("section_path") or "").lower()
        text = _normalize_query_text(row.get("content") or "").lower()
        score = 0.0
        if howto_like:
            if any(marker in doc_title for marker in procedural_markers):
                score += 1.2
            if any(marker in section_title for marker in procedural_markers):
                score += 1.5
            if any(marker in section_path for marker in procedural_markers):
                score += 0.9
            if any(marker in text for marker in ("repo init", "repo sync", "./build.sh", "build/prebuilts_download.sh")):
                score += 1.4
            if any(marker in doc_title for marker in troubleshooting_markers):
                score -= 1.2
            if any(marker in section_title for marker in troubleshooting_markers):
                score -= 1.8
            if any(marker in section_path for marker in troubleshooting_markers):
                score -= 1.4
        overlap = sum(1 for token in query_tokens if token in doc_title or token in section_title or token in section_path)
        score += min(1.5, overlap * 0.35)
        return (score, float(row.get("rank_score") or row.get("rerank_score") or 0.0))

    ordered_results = sorted(ranked_results, key=fallback_priority, reverse=True)
    family_ordered_results = _order_rows_by_family_cohesion(ordered_results)

    pack_rows: List[dict] = []
    seen_identities: set[str] = set()
    seen_anchor_scopes: set[tuple[str, str]] = set()

    def append_row(candidate: dict) -> bool:
        identity = str(describe_context_chunk(candidate).get("identity") or "")
        if not identity or identity in seen_identities:
            return False
        seen_identities.add(identity)
        pack_rows.append(dict(candidate))
        return True

    for anchor in family_ordered_results:
        anchor_info = describe_context_chunk(anchor)
        anchor_scope = (str(anchor_info.get("doc_key") or ""), str(anchor_info.get("scope_key") or ""))
        if anchor_scope in seen_anchor_scopes:
            continue
        if len(seen_anchor_scopes) >= max(2, anchor_limit + 1):
            break
        seen_anchor_scopes.add(anchor_scope)
        doc_id = str(anchor.get("doc_id") or anchor_info.get("source_path") or "").strip()
        doc_chunks = load_doc_chunks(doc_id) if doc_id else []
        expanded_rows = _expand_anchor_evidence_rows(anchor, doc_chunks) or [anchor]
        for candidate in expanded_rows:
            append_row(candidate)
            if len(pack_rows) >= max(5, context_limit * 2):
                return pack_rows

    for candidate in ordered_results:
        append_row(candidate)
        if len(pack_rows) >= max(5, context_limit * 2):
            break
    return pack_rows or family_ordered_results[: max(5, context_limit * 2)]


def _extract_procedural_focus_terms(query: str) -> List[str]:
    query_lower = _normalize_query_text(query).lower()
    if not query_lower:
        return []
    stop_terms = {
        "how",
        "howto",
        "как",
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "this",
        "that",
        "what",
        "when",
        "where",
        "which",
        "guide",
        "steps",
        "step",
        "instruction",
        "instructions",
        "setup",
        "install",
        "configure",
        "initialize",
        "init",
        "build",
        "run",
        "sync",
        "prepare",
        "code",
        "local",
        "branch",
    }
    terms: List[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-zа-яё0-9][a-zа-яё0-9._/-]{2,}", query_lower):
        if token in stop_terms or token.isdigit():
            continue
        if token not in seen:
            terms.append(token)
            seen.add(token)
    return terms[:8]


def _extract_procedural_match_terms(query: str) -> List[str]:
    focus_terms = _extract_procedural_focus_terms(query)
    if focus_terms:
        return focus_terms
    query_lower = _normalize_query_text(query).lower()
    fallback_terms: List[str] = []
    for marker in ("sync", "build", "install", "setup", "configure", "initialize", "init", "run", "prepare"):
        if marker in query_lower and marker not in fallback_terms:
            fallback_terms.append(marker)
    return fallback_terms


def _is_compound_howto_query(query: str) -> bool:
    query_lower = _normalize_query_text(query).lower()
    if not query_lower:
        return False
    focus_terms = _extract_procedural_focus_terms(query_lower)
    has_explicit_howto_cue = any(token in query_lower for token in ("how to", "как ", "steps", "guide"))
    procedural_action_markers = ("sync", "build", "install", "setup", "configure", "initialize", "init", "run", "prepare")
    action_hits = {marker for marker in procedural_action_markers if marker in query_lower}
    if has_explicit_howto_cue and len(action_hits) >= 2:
        return True
    return len(focus_terms) >= 2 and (has_explicit_howto_cue or len(action_hits) >= 2)


def _compute_procedural_family_score(query: str, row: dict) -> float:
    query_lower = _normalize_query_text(query).lower()
    query_tokens = set(re.findall(r"[a-zа-яё0-9]{3,}", query_lower))
    focus_terms = _extract_procedural_match_terms(query_lower)
    info = describe_context_chunk(row)
    meta = row.get("metadata") or {}
    doc_title = _normalize_query_text(meta.get("doc_title") or meta.get("title") or "").lower()
    section_title = _normalize_query_text(meta.get("section_title") or "").lower()
    section_path = _normalize_query_text(meta.get("section_path") or "").lower()
    source_path = _normalize_query_text(row.get("source_path") or "").lower()
    text = _normalize_query_text(row.get("content") or "").lower()
    procedural_markers = ("how to", "guide", "steps", "initialize", "setup", "install", "configure", "prepare", "sync", "build", "run")
    troubleshooting_markers = (
        "issue",
        "issues",
        "error",
        "errors",
        "fix",
        "fixes",
        "patch",
        "workaround",
        "debug",
        "troubleshoot",
        "regeneration",
        "failed",
        "failure",
    )
    procedural_hits = 0
    score = float(row.get("rank_score") or row.get("rerank_score") or 0.0)
    for field_value, weight in (
        (doc_title, 1.0),
        (section_title, 1.3),
        (section_path, 0.9),
        (source_path, 0.8),
    ):
        if any(marker in field_value for marker in procedural_markers):
            score += weight
        field_term_hits = sum(1 for term in focus_terms if term in field_value)
        if field_term_hits:
            score += min(weight * 1.8, field_term_hits * (0.45 + weight * 0.2))
            procedural_hits += field_term_hits
            if field_term_hits >= 2:
                score += 0.35 * weight
        if any(marker in field_value for marker in troubleshooting_markers):
            score -= weight * 1.4
    text_term_hits = sum(1 for term in focus_terms if term in text)
    if text_term_hits:
        score += min(1.6, text_term_hits * 0.35)
        procedural_hits += min(text_term_hits, 2)
    if re.search(r"(^|[\n`\s])(\./|repo\s|patch -p|export\s|hb\s|gn\s|ninja\s|pip\s|npm\s)", text):
        score += 0.8
        if text_term_hits:
            score += 0.6
            procedural_hits += 1
    overlap = sum(1 for token in query_tokens if token in doc_title or token in section_title or token in section_path or token in source_path)
    score += min(1.6, overlap * 0.3)
    if re.search(r"\bv\d{2,4}\b", " ".join((doc_title, section_title, section_path, source_path))) and procedural_hits < 3:
        score -= 1.0
    if "feature" in source_path and procedural_hits < 3:
        score -= 0.6
    score += 0.2 if "doc:" in str(info.get("scope_key") or "") else 0.0
    return score


def _focus_compound_howto_rows(query: str, ranked_results: List[dict]) -> List[dict]:
    if not ranked_results or not _is_compound_howto_query(query):
        return ranked_results

    family_buckets: Dict[str, List[dict]] = {}
    family_scores: Dict[str, float] = {}
    family_best: Dict[str, float] = {}
    family_compound_hits: Dict[str, int] = {}
    for row in ranked_results:
        info = describe_context_chunk(row)
        family_key = str(info.get("doc_key") or info.get("source_path") or info.get("identity") or "")
        if not family_key:
            continue
        score = _compute_procedural_family_score(query, row)
        family_buckets.setdefault(family_key, []).append(row)
        family_scores[family_key] = family_scores.get(family_key, 0.0) + score
        family_best[family_key] = max(family_best.get(family_key, float("-inf")), score)
        combined_text = " ".join(
            [
                _normalize_query_text(row.get("source_path") or "").lower(),
                _normalize_query_text((row.get("metadata") or {}).get("doc_title") or (row.get("metadata") or {}).get("title") or "").lower(),
                _normalize_query_text((row.get("metadata") or {}).get("section_title") or "").lower(),
                _normalize_query_text((row.get("metadata") or {}).get("section_path") or "").lower(),
                _normalize_query_text(row.get("content") or "").lower(),
            ]
        )
        focus_terms = _extract_procedural_match_terms(query)
        hit_count = sum(1 for term in focus_terms if term in combined_text)
        if re.search(r"(^|[\n`\s])(\./|repo\s|patch -p|export\s|hb\s|gn\s|ninja\s|pip\s|npm\s)", combined_text):
            hit_count += 1
        family_compound_hits[family_key] = max(family_compound_hits.get(family_key, 0), hit_count)

    if not family_buckets:
        return ranked_results

    ordered_families = sorted(
        family_buckets.keys(),
        key=lambda key: (family_best.get(key, float("-inf")), family_scores.get(key, float("-inf")), len(family_buckets.get(key, []))),
        reverse=True,
    )
    best_family = ordered_families[0]
    best_score = family_best.get(best_family, float("-inf"))
    best_total = family_scores.get(best_family, float("-inf"))
    second_score = family_best.get(ordered_families[1], float("-inf")) if len(ordered_families) > 1 else float("-inf")
    second_total = family_scores.get(ordered_families[1], float("-inf")) if len(ordered_families) > 1 else float("-inf")
    family_rows = family_buckets.get(best_family) or []
    if best_score < 1.5:
        return ranked_results
    if family_compound_hits.get(best_family, 0) >= 3:
        return list(family_rows)
    if len(family_rows) < 2 and (best_score - second_score) < 0.4 and (best_total - second_total) < 0.6:
        return ranked_results

    focused = list(family_rows)
    if len(focused) >= 2:
        return focused
    return ranked_results


_EXACT_LOOKUP_NAVIGATION_PHRASES = (
    "where is",
    "where can i find",
    "where do i find",
    "where do i get",
    "how to find",
    "how to get the",
    "what patch",
    "which patch",
    "which page",
    "what page",
    "api reference",
    "official documentation",
    "official docs",
    "official guide",
    "где найти",
    "где находится",
    "где скачать",
    "где взять",
    "где получить",
    "какой патч",
    "официальная документация",
)

_EXACT_LOOKUP_MIN_FIELD_COVERAGE = 0.45
_EXACT_LOOKUP_MIN_CANONICALITY = 1.5


def _is_exact_lookup_query(query: str) -> bool:
    """Return True when the query is a navigation or exact artifact lookup request.

    The lane must not fire for open-ended compound procedural HOWTO queries —
    that lane handles multi-step build/sync/install procedures.
    """
    if not query:
        return False
    q = _normalize_query_text(query).lower()
    if not any(phrase in q for phrase in _EXACT_LOOKUP_NAVIGATION_PHRASES):
        return False
    if _is_compound_howto_query(query):
        return False
    return True


def _apply_exact_lookup_lane(
    query: str,
    rows: List[dict],
) -> Tuple[List[dict], str, Optional[str], Optional[str]]:
    """Apply exact-lookup lane for navigation/reference queries.

    Returns (focused_rows, query_mode, anchor_family, anchor_reason).
    query_mode is one of:
      - "generalized"          — lane did not fire (query shape not matched)
      - "exact_lookup"         — lane fired, anchor found, rows focused
      - "exact_lookup_degraded" — lane fired but no confident anchor; original order kept
    """
    if not rows or not _is_exact_lookup_query(query):
        return rows, "generalized", None, None

    # Find the best anchor candidate among the top candidates.
    anchor: Optional[dict] = None
    anchor_reason: Optional[str] = None
    for row in rows[:5]:
        exactish = bool(row.get("_query_field_exact_match")) or bool(row.get("_query_field_best_exact"))
        coverage = float(row.get("_query_field_best_coverage", 0.0))
        distinctive_hits = int(row.get("_query_field_distinctive_hits", 0))
        canonicality = float(row.get("_canonicality_score", 0.0))
        if exactish:
            anchor = row
            anchor_reason = "exact_field_match"
            break
        if coverage >= _EXACT_LOOKUP_MIN_FIELD_COVERAGE:
            anchor = row
            anchor_reason = f"field_coverage_{coverage:.2f}"
            break
        if distinctive_hits >= 1 and canonicality >= _EXACT_LOOKUP_MIN_CANONICALITY:
            anchor = row
            anchor_reason = "canonical_distinctive_hit"
            break

    if anchor is None:
        # No candidate met the confidence threshold — degrade gracefully.
        return rows, "exact_lookup_degraded", None, "no_confident_anchor"

    # Identify anchor's document family by source_path.
    anchor_source = str(anchor.get("source_path") or "").strip()
    anchor_family = anchor_source or str(
        (anchor.get("metadata") or {}).get("doc_title") or ""
    ).strip()

    # Focus: anchor-document rows first, then up to 2 rows from other families
    # for fallback coverage in case the anchor alone is insufficient.
    anchor_rows = [r for r in rows if str(r.get("source_path") or "") == anchor_source]
    other_rows = [r for r in rows if str(r.get("source_path") or "") != anchor_source]
    focused = anchor_rows + other_rows[:2]
    if not focused:
        focused = rows
    return focused, "exact_lookup", anchor_family, anchor_reason


def _decorate_candidate_with_context_trace(candidate: dict, trace: Optional[Dict[str, Any]]) -> dict:
    row = dict(candidate) if isinstance(candidate, dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    metadata_obj = dict(metadata)
    for key in (
        _CONTEXT_TRACE_SELECTED_KEY,
        _CONTEXT_TRACE_RANK_KEY,
        _CONTEXT_TRACE_REASON_KEY,
        _CONTEXT_TRACE_ANCHOR_RANK_KEY,
        _FAMILY_TRACE_KEY,
        _FAMILY_TRACE_RANK_KEY,
        _CANONICALITY_TRACE_SCORE_KEY,
        _CONTAMINATION_TRACE_PENALTY_KEY,
        _CANONICALITY_TRACE_REASON_KEY,
        _CONTAMINATION_TRACE_REASON_KEY,
    ):
        metadata_obj.pop(key, None)

    if trace:
        metadata_obj[_CONTEXT_TRACE_SELECTED_KEY] = True
        metadata_obj[_CONTEXT_TRACE_RANK_KEY] = trace.get("context_rank")
        metadata_obj[_CONTEXT_TRACE_REASON_KEY] = trace.get("context_reason")
        if trace.get("context_anchor_rank") is not None:
            metadata_obj[_CONTEXT_TRACE_ANCHOR_RANK_KEY] = trace.get("context_anchor_rank")
    family_key = str(row.get("_family_key") or "").strip()
    family_rank = _coerce_positive_int(row.get("_family_rank"))
    if family_key:
        metadata_obj[_FAMILY_TRACE_KEY] = family_key[:200]
        row["_family_key"] = family_key[:200]
    if family_rank is not None:
        metadata_obj[_FAMILY_TRACE_RANK_KEY] = family_rank
        row["_family_rank"] = family_rank
    canonicality_score = _metric_to_str(row.get("_canonicality_score"))
    contamination_penalty = _metric_to_str(row.get("_contamination_penalty"))
    canonicality_reason = str(row.get("_canonicality_reason") or "").strip()
    contamination_reason = str(row.get("_contamination_reason") or "").strip()
    if canonicality_score is not None:
        metadata_obj[_CANONICALITY_TRACE_SCORE_KEY] = canonicality_score
    if contamination_penalty is not None:
        metadata_obj[_CONTAMINATION_TRACE_PENALTY_KEY] = contamination_penalty
    if canonicality_reason:
        metadata_obj[_CANONICALITY_TRACE_REASON_KEY] = canonicality_reason[:200]
    if contamination_reason:
        metadata_obj[_CONTAMINATION_TRACE_REASON_KEY] = contamination_reason[:200]

    row["metadata"] = metadata_obj
    return row


def _extract_context_trace_from_metadata(metadata_obj: Optional[Dict[str, Any]]) -> tuple[bool, Optional[int], Optional[str], Optional[int], Optional[str], Optional[int], Optional[str], Optional[str], Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    if not isinstance(metadata_obj, dict):
        return False, None, None, None, None, None, None, None, None, None, metadata_obj
    clean_metadata = dict(metadata_obj)
    included_in_context = _coerce_bool(clean_metadata.pop(_CONTEXT_TRACE_SELECTED_KEY, None))
    context_rank = _coerce_positive_int(clean_metadata.pop(_CONTEXT_TRACE_RANK_KEY, None))
    context_reason_raw = clean_metadata.pop(_CONTEXT_TRACE_REASON_KEY, None)
    context_reason = str(context_reason_raw).strip()[:64] if context_reason_raw is not None else None
    context_anchor_rank = _coerce_positive_int(clean_metadata.pop(_CONTEXT_TRACE_ANCHOR_RANK_KEY, None))
    family_key_raw = clean_metadata.pop(_FAMILY_TRACE_KEY, None)
    family_key = str(family_key_raw).strip()[:200] if family_key_raw is not None else None
    family_rank = _coerce_positive_int(clean_metadata.pop(_FAMILY_TRACE_RANK_KEY, None))
    canonicality_score = _metric_to_str(clean_metadata.pop(_CANONICALITY_TRACE_SCORE_KEY, None))
    contamination_penalty = _metric_to_str(clean_metadata.pop(_CONTAMINATION_TRACE_PENALTY_KEY, None))
    canonicality_reason_raw = clean_metadata.pop(_CANONICALITY_TRACE_REASON_KEY, None)
    canonicality_reason = str(canonicality_reason_raw).strip()[:200] if canonicality_reason_raw is not None else None
    contamination_reason_raw = clean_metadata.pop(_CONTAMINATION_TRACE_REASON_KEY, None)
    contamination_reason = str(contamination_reason_raw).strip()[:200] if contamination_reason_raw is not None else None
    return (
        bool(included_in_context),
        context_rank,
        context_reason,
        context_anchor_rank,
        family_key,
        family_rank,
        canonicality_score,
        contamination_penalty,
        canonicality_reason,
        contamination_reason,
        clean_metadata,
    )


def _merge_context_diagnostics_candidates(
    *,
    ranked_results: List[dict],
    included_context_rows: List[dict],
) -> List[dict]:
    if not ranked_results and not included_context_rows:
        return []

    trace_by_token: Dict[str, Dict[str, Any]] = {}
    included_entries: List[tuple[set[str], dict]] = []
    for context_rank, row in enumerate(included_context_rows, start=1):
        trace = {
            "context_rank": context_rank,
            "context_reason": str(row.get("_context_reason") or "primary"),
            "context_anchor_rank": _coerce_positive_int(row.get("_context_anchor_rank")),
        }
        tokens = set(_build_context_trace_tokens(row))
        decorated = _decorate_candidate_with_context_trace(row, trace)
        included_entries.append((tokens, decorated))
        for token in tokens:
            trace_by_token.setdefault(token, trace)

    merged_candidates: List[dict] = []
    ranked_entries = [
        (index, set(_build_context_trace_tokens(candidate)), candidate)
        for index, candidate in enumerate(ranked_results or [])
    ]
    consumed_ranked_indexes: set[int] = set()
    emitted_token_sets: List[set[str]] = []

    for tokens, decorated in included_entries:
        matched_entry: Optional[tuple[int, set[str], dict]] = None
        for entry in ranked_entries:
            index, candidate_tokens, _candidate = entry
            if index in consumed_ranked_indexes:
                continue
            if tokens and candidate_tokens and tokens & candidate_tokens:
                matched_entry = entry
                break
        if matched_entry is not None:
            index, candidate_tokens, candidate = matched_entry
            consumed_ranked_indexes.add(index)
            trace = None
            for token in tokens:
                if token in trace_by_token:
                    trace = trace_by_token[token]
                    break
            merged_candidate = _decorate_candidate_with_context_trace(candidate, trace)
            if not merged_candidate.get("_family_key") and decorated.get("_family_key"):
                merged_candidate["_family_key"] = decorated.get("_family_key")
            if not merged_candidate.get("_family_rank") and decorated.get("_family_rank"):
                merged_candidate["_family_rank"] = decorated.get("_family_rank")
            merged_candidates.append(merged_candidate)
            emitted_token_sets.append(candidate_tokens or tokens)
            continue

        support_candidate = dict(decorated)
        support_candidate["origin"] = support_candidate.get("origin") or "context_support"
        support_candidate["channel"] = support_candidate.get("channel") or "context_support"
        merged_candidates.append(support_candidate)
        emitted_token_sets.append(tokens)

    for index, tokens, candidate in ranked_entries:
        if index in consumed_ranked_indexes:
            continue
        if tokens and any(tokens & existing for existing in emitted_token_sets):
            continue
        merged_candidates.append(_decorate_candidate_with_context_trace(candidate, None))
        emitted_token_sets.append(tokens)

    return merged_candidates


def _persist_retrieval_logs(
    *,
    db: Session,
    request_id: str,
    query: str,
    knowledge_base_id: Optional[int],
    intent: Optional[str],
    hints: Optional[Dict[str, Any]],
    filters: Optional[Dict[str, Any]],
    total_candidates: int,
    total_selected: int,
    latency_ms: int,
    backend_name: Optional[str],
    degraded_mode: bool = False,
    degraded_reason: Optional[str] = None,
    orchestrator_mode: Optional[str] = None,
    query_mode: Optional[str] = None,
    lookup_anchor_family: Optional[str] = None,
    lookup_anchor_reason: Optional[str] = None,
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> None:
    # Test doubles may pass lightweight DB stubs without persistence APIs.
    if not hasattr(db, "add") or not hasattr(db, "commit"):
        return
    hints_payload = hints if isinstance(hints, dict) else {}
    extra_hints: Dict[str, str] = {}
    if orchestrator_mode:
        extra_hints["orchestrator_mode"] = str(orchestrator_mode).strip().lower()
    if query_mode and query_mode != "generalized":
        extra_hints["query_mode"] = str(query_mode).strip()[:64]
    if lookup_anchor_family:
        extra_hints["lookup_anchor_family"] = str(lookup_anchor_family).strip()[:200]
    if lookup_anchor_reason:
        extra_hints["lookup_anchor_reason"] = str(lookup_anchor_reason).strip()[:120]
    if extra_hints:
        hints_payload = dict(hints_payload)
        hints_payload.update(extra_hints)
    try:
        channel_counts: Dict[str, int] = {}
        db.add(
            RetrievalQueryLog(
                request_id=request_id,
                knowledge_base_id=knowledge_base_id,
                query=query or "",
                intent=(intent or "")[:32] or None,
                hints_json=_safe_json_dumps(hints_payload),
                filters_json=_safe_json_dumps(filters),
                total_candidates=max(0, int(total_candidates or 0)),
                total_selected=max(0, int(total_selected or 0)),
                latency_ms=max(0, int(latency_ms or 0)),
                backend_name=(backend_name or "")[:32] or None,
                degraded_mode=bool(degraded_mode),
                degraded_reason=(degraded_reason or "")[:120] or None,
            )
        )
        if hasattr(db, "flush"):
            db.flush()
        for rank, candidate in enumerate((candidates or [])[:20], start=1):
            candidate_trace = dict(candidate) if isinstance(candidate, dict) else {}
            metadata_obj = candidate_trace.get("metadata") if isinstance(candidate_trace, dict) else {}
            if not isinstance(metadata_obj, dict):
                metadata_obj = {}
            metadata_obj = dict(metadata_obj)
            family_key = str(candidate_trace.get("_family_key") or "").strip()
            family_rank = _coerce_positive_int(candidate_trace.get("_family_rank"))
            if family_key:
                metadata_obj[_FAMILY_TRACE_KEY] = family_key[:200]
            if family_rank is not None:
                metadata_obj[_FAMILY_TRACE_RANK_KEY] = family_rank
            canonicality_score = _metric_to_str(candidate_trace.get("_canonicality_score"))
            contamination_penalty = _metric_to_str(candidate_trace.get("_contamination_penalty"))
            canonicality_reason = str(candidate_trace.get("_canonicality_reason") or "").strip()
            contamination_reason = str(candidate_trace.get("_contamination_reason") or "").strip()
            if canonicality_score is not None:
                metadata_obj[_CANONICALITY_TRACE_SCORE_KEY] = canonicality_score
            if contamination_penalty is not None:
                metadata_obj[_CONTAMINATION_TRACE_PENALTY_KEY] = contamination_penalty
            if canonicality_reason:
                metadata_obj[_CANONICALITY_TRACE_REASON_KEY] = canonicality_reason[:200]
            if contamination_reason:
                metadata_obj[_CONTAMINATION_TRACE_REASON_KEY] = contamination_reason[:200]
            metadata_json = _safe_json_dumps(metadata_obj if isinstance(metadata_obj, dict) else {})
            content_preview = ""
            if isinstance(candidate_trace, dict):
                content_preview = (candidate_trace.get("content") or "")[:1200]
            origin = _normalize_trace_token(candidate_trace.get("origin") or candidate_trace.get("channel"))
            channel = _normalize_trace_token(candidate_trace.get("channel") or candidate_trace.get("origin"))
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
            channel_rank = _coerce_positive_int(candidate_trace.get("channel_rank")) or channel_counts[channel]
            fusion_rank = _coerce_positive_int(candidate_trace.get("fusion_rank")) or rank
            fusion_score_value = _derive_fusion_score_value(candidate_trace)
            rerank_delta_value = _derive_rerank_delta_value(candidate_trace)
            db.add(
                RetrievalCandidateLog(
                    request_id=request_id,
                    rank=rank,
                    source_path=((candidate_trace.get("source_path") or "")[:500] if isinstance(candidate_trace, dict) else ""),
                    source_type=((candidate_trace.get("source_type") or "")[:50] if isinstance(candidate_trace, dict) else ""),
                    distance=_metric_to_str(candidate_trace.get("distance") if isinstance(candidate_trace, dict) else None),
                    rerank_score=_metric_to_str(candidate_trace.get("rerank_score") if isinstance(candidate_trace, dict) else None),
                    origin=origin,
                    channel=channel,
                    channel_rank=channel_rank,
                    fusion_rank=fusion_rank,
                    fusion_score=_metric_to_str(fusion_score_value) or "0.000000",
                    rerank_delta=_metric_to_str(rerank_delta_value),
                    metadata_json=metadata_json,
                    content_preview=content_preview,
                )
            )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            if hasattr(db, "rollback"):
                db.rollback()
        except Exception:
            pass
        logger.warning("Failed to persist retrieval logs request_id=%s: %s", request_id, exc)


@router.post(
    "/query",
    response_model=RAGAnswer,
    summary="Поиск ответа в базе знаний (RAG)",
    dependencies=[Depends(require_api_key)],
)
def rag_query(payload: RAGQuery, db: Session = Depends(get_db_dep)) -> RAGAnswer:  # noqa: ARG001
    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    kb_id = payload.knowledge_base_id
    request_id = uuid4().hex
    t0 = perf_counter()
    backend_name = getattr(rag_system, "retrieval_backend", "legacy")
    intent: Optional[str] = None
    query_hints: Dict[str, Any] = {}
    filters_payload: Dict[str, Any] = {}
    results: List[dict] = []
    filtered_results: List[dict] = []
    ranked_results: List[dict] = []
    orchestrator_mode: str = "legacy"
    exact_lookup_mode: str = "generalized"
    exact_lookup_anchor_family: Optional[str] = None
    exact_lookup_anchor_reason: Optional[str] = None

    try:
        # Настройки RAG
        try:
            from shared.config import (  # type: ignore
                RAG_TOP_K,
                RAG_CONTEXT_LENGTH,
                RAG_ENABLE_CITATIONS,
                RAG_MIN_RERANK_SCORE,
                RAG_DEBUG_RETURN_CHUNKS,
                RAG_ORCHESTRATOR_V4,
                RAG_LEGACY_QUERY_HEURISTICS,
            )
            top_k_search = payload.top_k or RAG_TOP_K
            top_k_for_context = RAG_TOP_K
            context_length = RAG_CONTEXT_LENGTH
            enable_citations = RAG_ENABLE_CITATIONS
            min_rerank_score = RAG_MIN_RERANK_SCORE
            debug_return_chunks = RAG_DEBUG_RETURN_CHUNKS
            orchestrator_v4_enabled = bool(RAG_ORCHESTRATOR_V4)
            legacy_query_heuristics_enabled = bool(RAG_LEGACY_QUERY_HEURISTICS)
        except Exception:  # noqa: BLE001
            top_k_search = payload.top_k or 10
            top_k_for_context = 8
            context_length = 1200
            enable_citations = True
            min_rerank_score = 0.0
            debug_return_chunks = False
            orchestrator_v4_enabled = False
            legacy_query_heuristics_enabled = False
        orchestrator_mode = "v4" if orchestrator_v4_enabled else "legacy"

        # Настройки KB (single-page и контекст)
        kb_settings = {}
        if kb_id:
            kb = db.query(KnowledgeBase).filter_by(id=kb_id).first()
            if kb:
                kb_settings = normalize_kb_settings(getattr(kb, "settings", None))
        rag_settings = (kb_settings or {}).get("rag") or {}
        single_page_mode = bool(rag_settings.get("single_page_mode", False))
        single_page_top_k = int(rag_settings.get("single_page_top_k", top_k_for_context))
        full_page_multiplier = int(rag_settings.get("full_page_context_multiplier", 5))

        if single_page_mode:
            top_k_for_context = min(top_k_for_context, max(1, single_page_top_k))

        use_legacy_query_heuristics = (not orchestrator_v4_enabled) and legacy_query_heuristics_enabled
        retrieval_core_mode = "legacy_heuristic" if use_legacy_query_heuristics else "generalized"
        precomputed_query_hints = _extract_query_hints(payload.query)
        precomputed_query_hints["retrieval_core_mode"] = retrieval_core_mode

        # Поиск кандидатов в RAG (dense + keyword + optional rerank)
        logger.debug("RAG query: query=%r, kb_id=%s, top_k=%s", payload.query, kb_id, top_k_search)
        if use_legacy_query_heuristics:
            results = rag_system.search(
                query=payload.query,
                knowledge_base_id=kb_id,
                top_k=top_k_search,
            ) or []
        else:
            results, rewrite_trace = _run_controlled_multi_query_search(
                query=payload.query,
                knowledge_base_id=kb_id,
                top_k=top_k_search,
                hints=precomputed_query_hints,
                enable_rewrites=_should_enable_controlled_rewrites(payload.query, precomputed_query_hints),
            )
            precomputed_query_hints.update(rewrite_trace)
        logger.debug("RAG search returned %d results", len(results))
        def _parse_dt(value: Optional[str]) -> Optional[datetime]:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return None

        date_from = _parse_dt(payload.date_from)
        date_to = _parse_dt(payload.date_to)
        source_types = [s.lower() for s in (payload.source_types or [])]
        languages = [s.lower() for s in (payload.languages or [])]
        path_prefixes = [p.lower() for p in (payload.path_prefixes or [])]
        filters_payload = {
            "source_types": source_types,
            "languages": languages,
            "path_prefixes": path_prefixes,
            "date_from": payload.date_from,
            "date_to": payload.date_to,
        }

        def _passes_filters(item: dict) -> bool:
            if source_types:
                if (item.get("source_type") or "").lower() not in source_types:
                    return False
            meta = item.get("metadata") or {}
            if languages:
                lang = (meta.get("language") or "").lower()
                if lang and lang not in languages:
                    return False
            if path_prefixes:
                sp = (item.get("source_path") or "").lower()
                if not any(sp.startswith(p) for p in path_prefixes):
                    return False
            if date_from or date_to:
                updated = meta.get("source_updated_at") or meta.get("updated_at")
                updated_dt = _parse_dt(updated if isinstance(updated, str) else None)
                if updated_dt:
                    if date_from and updated_dt < date_from:
                        return False
                    if date_to and updated_dt > date_to:
                        return False
            return True

        if payload.source_types or payload.languages or payload.path_prefixes or payload.date_from or payload.date_to:
            results = [r for r in results if _passes_filters(r)]
        if logger.isEnabledFor(logging.DEBUG):
            top_preview = []
            for r in results[:5]:
                meta = r.get("metadata") or {}
                top_preview.append(
                    {
                        "source_path": r.get("source_path"),
                        "doc_title": meta.get("doc_title") or meta.get("title"),
                        "section_path": meta.get("section_path"),
                        "chunk_kind": meta.get("chunk_kind"),
                        "distance": r.get("distance"),
                        "rerank_score": r.get("rerank_score"),
                    }
                )
            logger.debug("RAG top results preview: %s", top_preview)

        def get_doc_id(result: dict) -> str:
            source_path = result.get("source_path") or ""
            if source_path:
                return source_path
            meta = result.get("metadata") or {}
            return meta.get("doc_title") or meta.get("title") or "unknown"

        def base_score(result: dict) -> float:
            if "rerank_score" in result and result.get("rerank_score") is not None:
                try:
                    return float(result.get("rerank_score"))
                except (TypeError, ValueError):
                    return 0.0
            try:
                return -float(result.get("distance", 0.0))
            except (TypeError, ValueError):
                return 0.0

        def apply_boosts(query: str, result: dict, intent: str, hints: Optional[Dict[str, Any]] = None) -> float:
            score = base_score(result)
            meta = result.get("metadata") or {}
            doc_title = (meta.get("doc_title") or meta.get("title") or "").lower()
            section_title = (meta.get("section_title") or "").lower()
            section_path = (meta.get("section_path") or "").lower()
            text = (result.get("content") or "").lower()
            q = (query or "").lower()
            source_path = (result.get("source_path") or "").lower()
            hints = hints or {}
            definition_term = (hints.get("definition_term") or "").strip().lower()
            fact_terms = [str(t).lower() for t in (hints.get("fact_terms") or [])]
            year_tokens = [str(t) for t in (hints.get("year_tokens") or [])]
            strict_fact_terms = [str(t).lower() for t in (hints.get("strict_fact_terms") or [])]
            key_phrases = [str(t).lower() for t in (hints.get("key_phrases") or [])]
            metric_query = bool(hints.get("metric_query"))
            prefer_numeric = bool(hints.get("prefer_numeric"))

            if "unit test" in q or "unittest" in q or "tests" in q:
                if any(t in section_title for t in ("unit test", "unittest", "test")):
                    score += 2.0
                if any(t in doc_title for t in ("unit test", "unittest", "test")):
                    score += 1.0
                if any(t in text for t in ("unit test", "unittest")):
                    score += 1.0

            if intent == "HOWTO":
                procedural_markers = ("how to", "guide", "steps", "initialize", "setup", "install", "configure", "prepare")
                troubleshooting_markers = (
                    "issue",
                    "issues",
                    "error",
                    "errors",
                    "fix",
                    "fixes",
                    "patch",
                    "workaround",
                    "debug",
                    "troubleshoot",
                    "regeneration",
                    "failed",
                    "failure",
                )
                focus_terms = _extract_procedural_match_terms(query)
                if any(t in section_title for t in ("how to", "build", "run", "steps")):
                    score += 1.0
                if any(t in doc_title for t in procedural_markers):
                    score += 0.8
                if any(t in section_title for t in procedural_markers):
                    score += 1.0
                if any(t in section_path for t in procedural_markers):
                    score += 0.6
                if any(t in section_title for t in ("overview", "introduction")):
                    score -= 1.0
                if any(t in section_title for t in troubleshooting_markers):
                    score -= 2.4
                if any(t in doc_title for t in troubleshooting_markers):
                    score -= 1.8
                if any(t in section_path for t in troubleshooting_markers):
                    score -= 1.5
                title_path_hits = sum(
                    1
                    for term in focus_terms
                    if term in source_path or term in doc_title or term in section_title or term in section_path
                )
                if title_path_hits:
                    score += min(2.0, title_path_hits * 0.55)
                    if title_path_hits >= 2:
                        score += 0.5
                text_hits = sum(1 for term in focus_terms if term in text)
                if text_hits:
                    score += min(1.2, text_hits * 0.25)
                if focus_terms and re.search(r"(^|[\n`\s])(\./|repo\s|patch -p|export\s|hb\s|gn\s|ninja\s|pip\s|npm\s)", text):
                    score += 0.8

            if intent == "DEFINITION":
                definition_markers = (
                    "называется",
                    "определяется",
                    "это ",
                    "представляет собой",
                    "этап ",
                    "совокупность",
                )
                if any(m in text for m in definition_markers):
                    score += 1.2
                if any(m in section_title for m in ("определ", "глоссар", "термин")):
                    score += 1.0
                if any(m in section_path for m in ("определ", "глоссар", "термин")):
                    score += 0.8

                if definition_term:
                    if definition_term in text:
                        score += 2.2
                    if definition_term in section_title or definition_term in section_path:
                        score += 1.0

                query_terms = [t for t in re.findall(r"[а-яёa-z0-9]{4,}", q) if t not in {"какие", "какой", "какая", "когда", "документе"}]
                for term in query_terms[:8]:
                    if f"{term} -" in text or f"{term} —" in text or f"{term}: " in text:
                        score += 1.5
                        break

            if intent == "FACTOID":
                fact_hits = 0
                for term in fact_terms:
                    if term in text:
                        fact_hits += 1
                    if term in section_title or term in section_path:
                        score += 0.7
                score += min(2.4, fact_hits * 0.45)
                strict_hits = sum(1 for term in strict_fact_terms if term in text)
                score += min(2.0, strict_hits * 0.6)

                if "кто" in q and any(marker in text for marker in ("принимает", "определяет", "утверждает", "правительство", "президент")):
                    score += 1.3
                if "как часто" in q and any(marker in text for marker in ("ежегод", "ежекварт", "раз в", "не реже", "кажды")):
                    score += 1.3
                if any(marker in q for marker in ("целевой", "показатель", "объем", "ввп", "мощност")):
                    if re.search(r"\b\d+[.,]?\d*\b", text):
                        score += 1.2
                    if any(marker in text for marker in ("экзафлопс", "ввп", "процент", "млрд", "трлн", "услуг")):
                        score += 1.2
                for y in year_tokens:
                    if y in text or y in section_title or y in section_path:
                        score += 0.9

                if metric_query:
                    phrase_hits = sum(1 for phrase in key_phrases if phrase and phrase in text)
                    section_phrase_hits = sum(1 for phrase in key_phrases if phrase and (phrase in section_title or phrase in section_path))
                    score += min(2.4, phrase_hits * 0.9)
                    score += min(1.2, section_phrase_hits * 0.6)
                    if prefer_numeric:
                        if re.search(r"\b\d+[.,]?\d*\b", text):
                            score += 1.6
                        else:
                            score -= 0.8
                    if ("на какой период" in q or "период рассчитана" in q) and re.search(r"до\s+20\d{2}", text):
                        score += 2.2
                    if "мощност" in q and "суперкомпьют" in q and "экзафлопс" in text:
                        score += 2.0
                    if "механизмы реализации" in q and any(
                        marker in text
                        for marker in ("дорожн", "национальн", "государственн программ", "планы мероприятий")
                    ):
                        score += 1.6
                    if "федеральные законы" in q and any(marker in text for marker in ("федеральн", "закон", "№")):
                        score += 1.4
                    if phrase_hits == 0 and strict_hits == 0 and fact_hits < 2 and not year_tokens:
                        score -= 1.2

            point_matches = hints.get("point_numbers") or re.findall(r"пункт[а-я]*\s+(\d+)", q)
            if point_matches:
                for point_no in point_matches:
                    point_token = f"пункт {point_no}"
                    if point_token in text:
                        score += 1.5
                    if point_token in section_title or point_token in section_path:
                        score += 1.5
                    if re.search(rf"(?:^|\s){re.escape(point_no)}\.", text):
                        score += 2.0
                    if re.search(rf"(?:^|\s){re.escape(point_no)}\.", section_title) or re.search(
                        rf"(?:^|\s){re.escape(point_no)}\.",
                        section_path,
                    ):
                        score += 1.2

            return score

        def select_docs(intent: str, ranked: List[dict]) -> List[str]:
            doc_best: Dict[str, float] = {}
            for r in ranked[:20]:
                doc_id = r.get("doc_id") or get_doc_id(r)
                doc_best[doc_id] = max(doc_best.get(doc_id, -1e9), float(r.get("rank_score", 0.0)))

            if not doc_best:
                return []

            items = sorted(doc_best.items(), key=lambda x: x[1], reverse=True)
            top_doc, top_score = items[0]
            second_doc, second_score = (items[1] if len(items) > 1 else (None, None))

            if intent == "DEFINITION":
                if second_doc and (top_score - float(second_score)) < 0.2:
                    return [top_doc, second_doc]
                return [top_doc]

            if intent == "FACTOID":
                selected = [top_doc]
                if second_doc and (top_score - float(second_score)) < 0.45:
                    selected.append(second_doc)
                if len(items) > 2 and (top_score - float(items[2][1])) < 0.25:
                    selected.append(items[2][0])
                return selected

            if intent != "HOWTO":
                return [d for d, _ in items[:3]]

            if second_doc and (top_score - float(second_score)) < 0.3:
                return [top_doc, second_doc]
            return [top_doc]

        def fetch_keyword_fallback_chunks(
            kb_id_value: Optional[int],
            hints: Dict[str, Any],
            intent: str,
        ) -> List[dict]:
            if not kb_id_value:
                return []
            point_numbers = hints.get("point_numbers") or []
            definition_term = (hints.get("definition_term") or "").strip()
            fact_terms = [str(t).strip() for t in (hints.get("fact_terms") or []) if str(t).strip()]
            year_tokens = [str(y).strip() for y in (hints.get("year_tokens") or []) if str(y).strip()]
            strict_fact_terms = [str(t).strip() for t in (hints.get("strict_fact_terms") or []) if str(t).strip()]
            key_phrases = [str(t).strip() for t in (hints.get("key_phrases") or []) if str(t).strip()]
            metric_query = bool(hints.get("metric_query"))
            prefer_numeric = bool(hints.get("prefer_numeric"))
            conditions = []
            if intent == "DEFINITION" and definition_term and len(definition_term) >= 3:
                conditions.append(KnowledgeChunk.content.ilike(f"%{definition_term}%"))
            if intent == "FACTOID":
                for term in fact_terms[:8]:
                    if len(term) >= 3:
                        conditions.append(KnowledgeChunk.content.ilike(f"%{term}%"))
                        conditions.append(KnowledgeChunk.chunk_metadata.ilike(f"%{term}%"))
                for term in strict_fact_terms[:6]:
                    if len(term) >= 4:
                        conditions.append(KnowledgeChunk.content.ilike(f"%{term}%"))
                for phrase in key_phrases[:4]:
                    if len(phrase) >= 6:
                        conditions.append(KnowledgeChunk.content.ilike(f"%{phrase}%"))
                        conditions.append(KnowledgeChunk.chunk_metadata.ilike(f"%{phrase}%"))
            for point_no in point_numbers:
                conditions.append(KnowledgeChunk.content.ilike(f"%пункт {point_no}%"))
                conditions.append(KnowledgeChunk.content.ilike(f"%{point_no}.%"))
                conditions.append(KnowledgeChunk.chunk_metadata.ilike(f"%{point_no}%"))
            for year in year_tokens:
                conditions.append(KnowledgeChunk.content.ilike(f"%{year}%"))
                conditions.append(KnowledgeChunk.chunk_metadata.ilike(f"%{year}%"))
            if not conditions:
                return []

            rows = (
                db.query(KnowledgeChunk)
                .filter(KnowledgeChunk.knowledge_base_id == kb_id_value)
                .filter(KnowledgeChunk.is_deleted == False)
                .filter(or_(*conditions))
                .limit(40)
                .all()
            )
            if not rows:
                return []

            fallback_results = []
            for row in rows:
                content = row.content or ""
                if len(content.strip()) < 20:
                    continue
                try:
                    meta = json.loads(row.chunk_metadata) if row.chunk_metadata else {}
                except Exception:
                    meta = {}

                text = content.lower()
                section_title = (meta.get("section_title") or "").lower()
                section_path = (meta.get("section_path") or "").lower()
                fallback_score = 0.0
                point_hit = False
                year_hit = False

                if definition_term and definition_term.lower() in text:
                    fallback_score += 3.0
                if intent == "DEFINITION":
                    if any(marker in text for marker in (" - ", " — ", ": ", "называется", "определяется", "представляет собой")):
                        fallback_score += 1.5
                    if any(token in section_title for token in ("определ", "термин", "глоссар")):
                        fallback_score += 1.0
                if intent == "FACTOID":
                    term_hits = sum(1 for term in fact_terms if term.lower() in text)
                    fallback_score += min(3.0, term_hits * 0.55)
                    strict_hits = sum(1 for term in strict_fact_terms if term.lower() in text)
                    fallback_score += min(2.5, strict_hits * 0.75)
                    phrase_hits = sum(1 for phrase in key_phrases if phrase.lower() in text)
                    fallback_score += min(2.8, phrase_hits * 1.0)
                    if any(marker in text for marker in ("утвержда", "принима", "ежегод", "раз в", "показател", "экзафлопс", "ввп", "процент")):
                        fallback_score += 1.1
                    if re.search(r"\b\d+[.,]?\d*\b", text):
                        fallback_score += 0.8
                    if metric_query and prefer_numeric:
                        if re.search(r"\b\d+[.,]?\d*\b", text):
                            fallback_score += 1.2
                        else:
                            fallback_score -= 0.6
                    if metric_query and ("на какой период" in (hints.get("original_query") or "").lower() or "период рассчитана" in (hints.get("original_query") or "").lower()):
                        if re.search(r"до\s+20\d{2}", text):
                            fallback_score += 2.0
                    for year in year_tokens:
                        if year in text:
                            fallback_score += 0.7
                            year_hit = True

                for point_no in point_numbers:
                    if f"пункт {point_no}" in text:
                        fallback_score += 2.0
                        point_hit = True
                    if re.search(rf"(?:^|\s){re.escape(point_no)}\.", text):
                        fallback_score += 2.5
                        point_hit = True
                    if f"пункт {point_no}" in section_title or f"пункт {point_no}" in section_path:
                        fallback_score += 2.0
                        point_hit = True
                    if re.search(rf"(?:^|\s){re.escape(point_no)}\.", section_title) or re.search(
                        rf"(?:^|\s){re.escape(point_no)}\.",
                        section_path,
                    ):
                        fallback_score += 1.2
                        point_hit = True

                if intent == "FACTOID" and metric_query:
                    has_min_overlap = (
                        sum(1 for term in strict_fact_terms if term.lower() in text) > 0
                        or sum(1 for phrase in key_phrases if phrase.lower() in text) > 0
                        or point_hit
                        or year_hit
                    )
                    if not has_min_overlap:
                        continue

                if fallback_score <= 0.0:
                    continue

                distance = -0.1 - min(fallback_score, 4.0) / 10.0
                fallback_results.append(
                    {
                        "id": row.id,
                        "content": content,
                        "metadata": meta,
                        "source_type": row.source_type,
                        "source_path": row.source_path,
                        "distance": distance,
                        "fallback_score": fallback_score,
                    }
                )

            fallback_results.sort(key=lambda item: float(item.get("fallback_score", 0.0)), reverse=True)
            return fallback_results[:20]

        def load_doc_chunks(doc_id: str) -> List[dict]:
            return _load_doc_chunks_for_context(db, doc_id, kb_id=kb_id)

        if not use_legacy_query_heuristics:
            intent = "GENERAL"
            query_hints = dict(precomputed_query_hints)
        else:
            intent = _infer_query_intent(payload.query, precomputed_query_hints)
            query_hints = dict(precomputed_query_hints)

        if use_legacy_query_heuristics and kb_id and (
            intent in {"DEFINITION", "FACTOID"}
            or query_hints.get("point_numbers")
            or query_hints.get("year_tokens")
        ):
            fallback_chunks = fetch_keyword_fallback_chunks(kb_id, query_hints, intent)
            if fallback_chunks:
                existing_keys = {
                    ((r.get("source_path") or ""), (r.get("content") or "")[:200])
                    for r in results
                }
                added = 0
                for chunk in fallback_chunks:
                    key = ((chunk.get("source_path") or ""), (chunk.get("content") or "")[:200])
                    if key in existing_keys:
                        continue
                    results.append(chunk)
                    existing_keys.add(key)
                    added += 1
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("RAG keyword fallback added %d chunks (intent=%s, hints=%s)", added, intent, query_hints)

        if not results:
            degraded_mode, degraded_reason = _derive_degraded_mode(
                backend_name=backend_name,
                candidates=results,
            )
            _persist_retrieval_logs(
                db=db,
                request_id=request_id,
                query=payload.query,
                knowledge_base_id=kb_id,
                intent=intent,
                hints=query_hints,
                filters=filters_payload,
                total_candidates=0,
                total_selected=0,
                latency_ms=int((perf_counter() - t0) * 1000),
                backend_name=backend_name,
                degraded_mode=degraded_mode,
                degraded_reason=degraded_reason,
                orchestrator_mode=orchestrator_mode,
                query_mode=exact_lookup_mode,
                lookup_anchor_family=exact_lookup_anchor_family,
                lookup_anchor_reason=exact_lookup_anchor_reason,
                candidates=[],
            )
            return RAGAnswer(answer="", sources=[], request_id=request_id)

        # Анти-галлюцинации: если есть reranker и все score ниже порога – считаем, что ответа нет
        rerank_scores = []
        for item in results:
            if item.get("rerank_score") is None:
                continue
            try:
                rerank_scores.append(float(item.get("rerank_score")))
            except (ValueError, TypeError):
                continue
        if min_rerank_score > 0.0 and rerank_scores and max(rerank_scores) < min_rerank_score:
            logger.debug("Best rerank score %f below threshold %f", max(rerank_scores), min_rerank_score)
            degraded_mode, degraded_reason = _derive_degraded_mode(
                backend_name=backend_name,
                candidates=results,
            )
            _persist_retrieval_logs(
                db=db,
                request_id=request_id,
                query=payload.query,
                knowledge_base_id=kb_id,
                intent=intent,
                hints=query_hints,
                filters=filters_payload,
                total_candidates=len(results),
                total_selected=0,
                latency_ms=int((perf_counter() - t0) * 1000),
                backend_name=backend_name,
                degraded_mode=degraded_mode,
                degraded_reason=degraded_reason,
                orchestrator_mode=orchestrator_mode,
                query_mode=exact_lookup_mode,
                lookup_anchor_family=exact_lookup_anchor_family,
                lookup_anchor_reason=exact_lookup_anchor_reason,
                candidates=results,
            )
            return RAGAnswer(answer="", sources=[], request_id=request_id)

        for r in results:
            doc_id = get_doc_id(r)
            r["doc_id"] = doc_id
            if not use_legacy_query_heuristics:
                r["rank_score"] = _metric_to_float(r.get("multi_query_score"))
                if r["rank_score"] is None:
                    r["rank_score"] = base_score(r)
                r["field_match_score"] = _generalized_field_match_score(payload.query, precomputed_query_hints, r)
                r["rank_score"] += float(r.get("field_match_score") or 0.0)
            else:
                r["rank_score"] = apply_boosts(payload.query, r, intent, query_hints)
            ranked_results.append(r)
        ranked_results.sort(key=lambda x: x.get("rank_score", 0.0), reverse=True)

        if use_legacy_query_heuristics and single_page_mode and intent in {"DEFINITION", "FACTOID"}:
            top_k_for_context = max(top_k_for_context, min(8, len(ranked_results)))
        if use_legacy_query_heuristics and intent == "FACTOID":
            top_k_for_context = min(max(top_k_for_context, 4), 6)

        selected_docs = select_docs(intent if use_legacy_query_heuristics else "GENERAL", ranked_results)
        filtered_results = [r for r in ranked_results if r.get("doc_id") in selected_docs] or ranked_results
        context_candidate_rows = _order_candidates_by_query_field_specificity(filtered_results, query=payload.query)
        context_candidate_rows = _order_rows_by_family_cohesion(context_candidate_rows)
        context_candidate_rows = _order_candidates_by_canonicality(context_candidate_rows, query=payload.query)
        context_candidate_rows = _focus_compound_howto_rows(payload.query, context_candidate_rows)
        context_candidate_rows, exact_lookup_mode, exact_lookup_anchor_family, exact_lookup_anchor_reason = (
            _apply_exact_lookup_lane(payload.query, context_candidate_rows)
        )
        if context_candidate_rows:
            filtered_results = context_candidate_rows
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("RAG intent=%s selected_docs=%s", intent, selected_docs)
            logger.debug("RAG filtered_results=%d", len(filtered_results))
            logger.debug("RAG context_candidate_rows=%d", len(context_candidate_rows))

        # Формируем контекст для LLM
        context_parts: list[str] = []
        token_limit_chars = max(3000, context_length * max(3, full_page_multiplier))
        selected_context_rows = _select_evidence_pack_rows(
            ranked_results=context_candidate_rows,
            load_doc_chunks=load_doc_chunks,
            anchor_limit=max(1, top_k_for_context),
            context_limit=max(3, top_k_for_context),
        )
        if not selected_context_rows:
            selected_context_rows = context_candidate_rows[: max(3, top_k_for_context)]

        used_chars = 0
        included_context_rows: List[dict] = []
        for row in selected_context_rows:
            block = _build_context_block(
                query=payload.query,
                row=row,
                base_context_length=context_length,
                full_multiplier=full_page_multiplier,
                enable_citations=enable_citations,
            )
            if not block:
                continue
            if context_parts and (used_chars + len(block)) > token_limit_chars:
                break
            context_parts.append(block)
            used_chars += len(block)
            included_context_rows.append(row)

        if not context_parts:
            logger.warning("No valid context parts extracted from results")
            degraded_mode, degraded_reason = _derive_degraded_mode(
                backend_name=backend_name,
                candidates=filtered_results,
            )
            _persist_retrieval_logs(
                db=db,
                request_id=request_id,
                query=payload.query,
                knowledge_base_id=kb_id,
                intent=intent,
                hints=query_hints,
                filters=filters_payload,
                total_candidates=len(results),
                total_selected=len(filtered_results),
                latency_ms=int((perf_counter() - t0) * 1000),
                backend_name=backend_name,
                degraded_mode=degraded_mode,
                degraded_reason=degraded_reason,
                orchestrator_mode=orchestrator_mode,
                query_mode=exact_lookup_mode,
                lookup_anchor_family=exact_lookup_anchor_family,
                lookup_anchor_reason=exact_lookup_anchor_reason,
                candidates=ranked_results or filtered_results,
            )
            return RAGAnswer(answer="", sources=[], request_id=request_id)

        context_text = "\n\n".join(context_parts)
        if logger.isEnabledFor(logging.DEBUG):
            source_ids = []
            selection_reasons = []
            for row, block in zip(included_context_rows, context_parts):
                selection_reasons.append(str(row.get("_context_reason") or "primary"))
                for line in block.splitlines():
                    if line.startswith("SOURCE_ID:"):
                        source_ids.append(line.split(":", 1)[1].strip())
                        break
            logger.debug(
                "RAG context parts=%d, chars=%d, source_ids=%s, reasons=%s",
                len(context_parts),
                len(context_text),
                source_ids,
                selection_reasons,
            )
            if os.getenv("RAG_DEBUG_LOG_CONTEXT", "false").lower() == "true":
                preview_blocks = []
                for block in context_parts:
                    preview_blocks.append(block[:500])
                logger.debug("RAG context preview: %s", preview_blocks)

        grounded_url_allowlist = _collect_grounded_url_allowlist(context_candidate_rows)
        security_decision = assess_query_security(payload.query)
        poisoned_rows = find_poisoned_context_rows(included_context_rows)
        security_flags = list(security_decision.get("flags") or [])
        if poisoned_rows:
            security_flags.extend(["suspicious_document", "screened_document"])
        if security_flags:
            query_hints["security_flags"] = sorted(set(security_flags))
        refusal_reason = ""
        if bool(security_decision.get("should_refuse")):
            refusal_reason = str(security_decision.get("reason") or "")
        elif poisoned_rows:
            refusal_reason = "poisoned_context"

        sources = _build_rag_sources(included_context_rows)

        if refusal_reason:
            ai_answer = build_security_refusal_message(payload.query, refusal_reason)
            degraded_mode, degraded_reason = _derive_degraded_mode(
                backend_name=backend_name,
                candidates=filtered_results,
            )
            diagnostics_candidates = _merge_context_diagnostics_candidates(
                ranked_results=ranked_results or filtered_results,
                included_context_rows=included_context_rows,
            )
            _persist_retrieval_logs(
                db=db,
                request_id=request_id,
                query=payload.query,
                knowledge_base_id=kb_id,
                intent=intent,
                hints=query_hints,
                filters=filters_payload,
                total_candidates=len(results),
                total_selected=len(filtered_results),
                latency_ms=int((perf_counter() - t0) * 1000),
                backend_name=backend_name,
                degraded_mode=degraded_mode,
                degraded_reason=degraded_reason,
                orchestrator_mode=orchestrator_mode,
                query_mode=exact_lookup_mode,
                lookup_anchor_family=exact_lookup_anchor_family,
                lookup_anchor_reason=exact_lookup_anchor_reason,
                candidates=diagnostics_candidates,
            )
            return RAGAnswer(answer=ai_answer, sources=sources, request_id=request_id)

        # Вызываем LLM через общий ai_manager
        logger.debug("Creating prompt for LLM query")
        prompt = create_prompt_with_language(
            payload.query,
            context_text,
            task="answer",
            enable_citations=enable_citations,
        )
        if os.getenv("RAG_DEBUG_LOG_PROMPT", "false").lower() == "true":
            logger.debug("RAG prompt preview: %s", prompt[:800])

        logger.debug("Calling AI manager with prompt length %d", len(prompt))
        ai_answer = ai_manager.query(prompt)
        provider_error = _classify_provider_transport_error(ai_answer)
        if provider_error:
            fallback_rows = _select_provider_fallback_rows(
                query=payload.query,
                ranked_results=context_candidate_rows,
                load_doc_chunks=load_doc_chunks,
                anchor_limit=max(1, top_k_for_context),
                context_limit=max(3, top_k_for_context),
            ) or selected_context_rows or included_context_rows
            ai_answer = _build_extractive_fallback_answer(
                payload.query,
                fallback_rows,
                failure_reason=provider_error,
            )
            sources = _build_rag_sources(fallback_rows)
        ai_answer = _postprocess_grounded_answer(
            ai_answer,
            context_text=context_text,
            grounded_url_allowlist=grounded_url_allowlist,
        )
        logger.debug("AI manager returned answer length %d", len(ai_answer) if ai_answer else 0)
        
        # Возвращаем сырой markdown от LLM
        # Форматирование (clean_citations, format_commands_in_text, format_markdown_to_html)
        # будет выполнено в bot handler через format_for_telegram_answer()

        # Debug mode: возвращаем первые N чанков с метаданными
        debug_chunks = None
        if debug_return_chunks:
            debug_chunks = []
            for chunk in filtered_results[:5]:  # Первые 5 чанков
                try:
                    metadata = chunk.get("metadata") or {}
                    debug_chunks.append({
                        "content": (chunk.get("content") or "")[:500],  # Первые 500 символов
                        "source_path": chunk.get("source_path") or "",
                        "score": float(chunk.get("distance", 0.0)),
                        "rerank_score": float(chunk.get("rerank_score", 0.0)),
                        "chunk_kind": metadata.get("chunk_kind", "text"),
                        "section_path": metadata.get("section_path", ""),
                        "doc_title": metadata.get("doc_title") or metadata.get("title", ""),
                        "code_lang": metadata.get("code_lang", ""),
                    })
                except Exception as e:  # noqa: BLE001
                    logger.warning("Error creating debug chunk: %s", e, exc_info=True)
                    continue

        degraded_mode, degraded_reason = _derive_degraded_mode(
            backend_name=backend_name,
            candidates=filtered_results,
        )
        diagnostics_candidates = _merge_context_diagnostics_candidates(
            ranked_results=ranked_results or filtered_results,
            included_context_rows=included_context_rows,
        )
        _persist_retrieval_logs(
            db=db,
            request_id=request_id,
            query=payload.query,
            knowledge_base_id=kb_id,
            intent=intent,
            hints=query_hints,
            filters=filters_payload,
            total_candidates=len(results),
            total_selected=len(filtered_results),
            latency_ms=int((perf_counter() - t0) * 1000),
            backend_name=backend_name,
            degraded_mode=degraded_mode,
            degraded_reason=degraded_reason,
            orchestrator_mode=orchestrator_mode,
            query_mode=exact_lookup_mode,
            lookup_anchor_family=exact_lookup_anchor_family,
            lookup_anchor_reason=exact_lookup_anchor_reason,
            candidates=diagnostics_candidates,
        )
        return RAGAnswer(answer=ai_answer, sources=sources, request_id=request_id, debug_chunks=debug_chunks)

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Error in RAG query endpoint: query=%r, kb_id=%s, error=%s",
            payload.query,
            kb_id,
            e,
            exc_info=True,
        )
        degraded_mode, degraded_reason = _derive_degraded_mode(
            backend_name=backend_name,
            candidates=(filtered_results or results),
        )
        _persist_retrieval_logs(
            db=db,
            request_id=request_id,
            query=payload.query,
            knowledge_base_id=kb_id,
            intent=intent,
            hints=query_hints,
            filters=filters_payload,
            total_candidates=len(results),
            total_selected=len(filtered_results),
            latency_ms=int((perf_counter() - t0) * 1000),
            backend_name=backend_name,
            degraded_mode=degraded_mode,
            degraded_reason=degraded_reason,
            orchestrator_mode=orchestrator_mode,
            query_mode=exact_lookup_mode,
            lookup_anchor_family=exact_lookup_anchor_family,
            lookup_anchor_reason=exact_lookup_anchor_reason,
            candidates=ranked_results or filtered_results or results,
        )
        return RAGAnswer(answer="", sources=[], request_id=request_id)

@router.get(
    "/diagnostics/{request_id}",
    response_model=RAGDiagnosticsResponse,
    summary="Диагностика retrieval по request_id",
    dependencies=[Depends(require_api_key)],
)
def rag_diagnostics(request_id: str, db: Session = Depends(get_db_dep)) -> RAGDiagnosticsResponse:
    query_log = db.query(RetrievalQueryLog).filter_by(request_id=request_id).first()
    if not query_log:
        raise HTTPException(status_code=404, detail="request_id not found")

    candidate_rows = (
        db.query(RetrievalCandidateLog)
        .filter_by(request_id=request_id)
        .order_by(RetrievalCandidateLog.rank.asc())
        .all()
    )
    candidates: List[RAGDiagnosticsCandidate] = []
    for fallback_rank, row in enumerate(candidate_rows, start=1):
        meta_obj = _safe_json_loads(getattr(row, "metadata_json", None))
        (
            included_in_context,
            context_rank,
            context_reason,
            context_anchor_rank,
            family_key,
            family_rank,
            canonicality_score,
            contamination_penalty,
            canonicality_reason,
            contamination_reason,
            meta_obj,
        ) = _extract_context_trace_from_metadata(meta_obj)
        trace = {
            "fusion_score": getattr(row, "fusion_score", None),
            "rerank_score": getattr(row, "rerank_score", None),
            "distance": getattr(row, "distance", None),
            "rerank_delta": getattr(row, "rerank_delta", None),
        }
        rank = _coerce_positive_int(getattr(row, "rank", None)) or fallback_rank
        origin = _normalize_trace_token(getattr(row, "origin", None) or getattr(row, "channel", None))
        channel = _normalize_trace_token(getattr(row, "channel", None) or getattr(row, "origin", None))
        channel_rank = _coerce_positive_int(getattr(row, "channel_rank", None)) or rank
        fusion_rank = _coerce_positive_int(getattr(row, "fusion_rank", None)) or rank
        candidates.append(
            RAGDiagnosticsCandidate(
                rank=rank,
                source_path=getattr(row, "source_path", "") or "",
                source_type=getattr(row, "source_type", "") or "",
                distance=getattr(row, "distance", None),
                rerank_score=getattr(row, "rerank_score", None),
                origin=origin,
                channel=channel,
                channel_rank=channel_rank,
                fusion_rank=fusion_rank,
                fusion_score=_metric_to_str(_derive_fusion_score_value(trace)) or "0.000000",
                rerank_delta=_metric_to_str(_derive_rerank_delta_value(trace)),
                included_in_context=included_in_context,
                context_rank=context_rank,
                context_reason=context_reason,
                context_anchor_rank=context_anchor_rank,
                family_key=family_key,
                family_rank=family_rank,
                canonicality_score=canonicality_score,
                contamination_penalty=contamination_penalty,
                canonicality_reason=canonicality_reason,
                contamination_reason=contamination_reason,
                metadata=meta_obj,
                content_preview=getattr(row, "content_preview", None),
            )
        )

    hints_obj = _safe_json_loads(query_log.hints_json)
    orchestrator_mode = _extract_hint_mode(hints_obj, "orchestrator_mode")
    retrieval_core_mode = _extract_hint_mode(hints_obj, "retrieval_core_mode")

    return RAGDiagnosticsResponse(
        request_id=query_log.request_id,
        query=query_log.query or "",
        knowledge_base_id=query_log.knowledge_base_id,
        intent=query_log.intent,
        orchestrator_mode=orchestrator_mode,
        retrieval_core_mode=retrieval_core_mode,
        backend_name=query_log.backend_name,
        total_candidates=int(query_log.total_candidates or 0),
        total_selected=int(query_log.total_selected or 0),
        latency_ms=int(query_log.latency_ms or 0),
        degraded_mode=bool(getattr(query_log, "degraded_mode", False)),
        degraded_reason=getattr(query_log, "degraded_reason", None),
        hints=hints_obj,
        filters=_safe_json_loads(query_log.filters_json),
        candidates=candidates,
    )


@router.post(
    "/eval/run",
    response_model=RAGEvalRunResponse,
    summary="Запустить RAG benchmark suite",
    dependencies=[Depends(require_api_key)],
)
def rag_eval_run(payload: RAGEvalRunRequest, db: Session = Depends(get_db_dep)) -> RAGEvalRunResponse:  # noqa: ARG001
    run_id = rag_eval_service.start_run(
        suite_name=(payload.suite or "rag-general-v1"),
        baseline_run_id=payload.baseline_run_id,
        slices=payload.slices,
        run_async=True,
    )
    return RAGEvalRunResponse(run_id=run_id, status="queued")


@router.get(
    "/eval/{run_id}",
    response_model=RAGEvalStatusResponse,
    summary="Статус RAG benchmark run",
    dependencies=[Depends(require_api_key)],
)
def rag_eval_status(run_id: str, db: Session = Depends(get_db_dep)) -> RAGEvalStatusResponse:  # noqa: ARG001
    status_obj = rag_eval_service.get_run_status(run_id)
    if not status_obj:
        raise HTTPException(status_code=404, detail="run_id not found")

    result_rows: List[RAGEvalResultRow] = []
    for row in status_obj.get("results") or []:
        details = _safe_json_loads((row or {}).get("details_json"))
        result_rows.append(
            RAGEvalResultRow(
                slice_name=str((row or {}).get("slice_name") or ""),
                metric_name=str((row or {}).get("metric_name") or ""),
                metric_value=float((row or {}).get("metric_value") or 0.0),
                threshold_value=(
                    float((row or {}).get("threshold_value"))
                    if (row or {}).get("threshold_value") is not None
                    else None
                ),
                passed=bool((row or {}).get("passed")),
                details=details,
            )
        )

    started_at = status_obj.get("started_at")
    finished_at = status_obj.get("finished_at")
    return RAGEvalStatusResponse(
        run_id=str(status_obj.get("run_id") or run_id),
        suite=str(status_obj.get("suite_name") or ""),
        baseline_run_id=(status_obj.get("baseline_run_id") or None),
        status=str(status_obj.get("status") or "unknown"),
        started_at=(started_at.isoformat() if hasattr(started_at, "isoformat") else None),
        finished_at=(finished_at.isoformat() if hasattr(finished_at, "isoformat") else None),
        metrics=(status_obj.get("metrics") or {}),
        error_message=(status_obj.get("error_message") or None),
        results=result_rows,
    )


@router.post(
    "/summary",
    response_model=RAGSummaryAnswer,
    summary="Сводка/FAQ/инструкция по материалам БЗ",
    dependencies=[Depends(require_api_key)],
)
def rag_summary(payload: RAGSummaryQuery, db: Session = Depends(get_db_dep)) -> RAGSummaryAnswer:  # noqa: ARG001
    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    kb_id = payload.knowledge_base_id
    top_k = payload.top_k or 8
    mode = (payload.mode or "summary").lower()

    results = rag_system.search(
        query=payload.query,
        knowledge_base_id=kb_id,
        top_k=top_k,
    ) or []

    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    date_from = _parse_dt(payload.date_from)
    date_to = _parse_dt(payload.date_to)

    def _passes_date(item: dict) -> bool:
        if not (date_from or date_to):
            return True
        meta = item.get("metadata") or {}
        updated = meta.get("source_updated_at") or meta.get("updated_at")
        updated_dt = _parse_dt(updated if isinstance(updated, str) else None)
        if updated_dt:
            if date_from and updated_dt < date_from:
                return False
            if date_to and updated_dt > date_to:
                return False
        return True

    if date_from or date_to:
        results = [r for r in results if _passes_date(r)]

    if not results:
        return RAGSummaryAnswer(answer="", sources=[])

    def load_doc_chunks(doc_id: str) -> List[dict]:
        return _load_doc_chunks_for_context(db, doc_id, kb_id=kb_id)

    context_candidate_rows = _order_candidates_by_query_field_specificity(results, query=payload.query)
    context_candidate_rows = _order_rows_by_family_cohesion(context_candidate_rows)
    context_candidate_rows = _order_candidates_by_canonicality(context_candidate_rows, query=payload.query)
    context_candidate_rows = _focus_compound_howto_rows(payload.query, context_candidate_rows)
    context_candidate_rows, _, _, _ = _apply_exact_lookup_lane(payload.query, context_candidate_rows)

    selected_context_rows = _select_evidence_pack_rows(
        ranked_results=context_candidate_rows,
        load_doc_chunks=load_doc_chunks,
        anchor_limit=max(1, top_k),
        context_limit=max(2, top_k),
    )
    if not selected_context_rows:
        selected_context_rows = context_candidate_rows[: max(2, top_k)]

    context_parts = []
    sources: List[RAGSource] = []
    seen = set()
    for r in selected_context_rows:
        info = describe_context_chunk(r)
        content_preview = build_query_focused_excerpt(
            payload.query,
            r.get("content") or "",
            max_length=1500,
            chunk_kind=str(info.get("chunk_kind") or "text"),
        )
        if not content_preview:
            continue
        context_parts.append(content_preview)
        sp = r.get("source_path") or ""
        st = r.get("source_type") or "unknown"
        _rmeta = r.get("metadata") or {}
        _rpage = _rmeta.get("page_no", _rmeta.get("page"))
        _rpage = _rpage if isinstance(_rpage, int) else None
        _rsection = (
            _rmeta.get("section_title")
            or _rmeta.get("section_path")
            or r.get("title")
            or None
        )
        key = (sp, _rpage)
        if sp and key not in seen:
            seen.add(key)
            sources.append(RAGSource(
                source_path=sp, source_type=st,
                score=float(r.get("rerank_score") or 0.0),
                page_number=_rpage,
                section_title=_rsection,
            ))

    context_text = "\n\n".join(context_parts)
    grounded_url_allowlist = _collect_grounded_url_allowlist(context_candidate_rows)
    security_decision = assess_query_security(payload.query)
    poisoned_rows = find_poisoned_context_rows(selected_context_rows)
    if bool(security_decision.get("should_refuse")) or poisoned_rows:
        refusal_reason = (
            str(security_decision.get("reason") or "")
            if bool(security_decision.get("should_refuse"))
            else "poisoned_context"
        )
        return RAGSummaryAnswer(
            answer=build_security_refusal_message(payload.query, refusal_reason),
            sources=_build_rag_sources(selected_context_rows),
        )

    if mode == "faq":
        system_task = "Составь FAQ: 5-10 вопросов и краткие ответы. Опирайся только на контекст."
    elif mode == "instructions":
        system_task = "Составь пошаговую инструкцию из 5-10 шагов. Опирайся только на контекст."
    else:
        system_task = "Сделай краткую сводку (5-10 пунктов) по контексту."

    prompt = (
        f"{system_task}\n\n"
        f"Вопрос: {payload.query}\n\n"
        f"Контекст:\n{context_text}\n"
    )

    answer = ai_manager.query(prompt)
    provider_error = _classify_provider_transport_error(answer)
    if provider_error:
        fallback_rows = _select_provider_fallback_rows(
            query=payload.query,
            ranked_results=context_candidate_rows,
            load_doc_chunks=load_doc_chunks,
            anchor_limit=max(1, top_k),
            context_limit=max(2, top_k),
        ) or selected_context_rows
        answer = _build_extractive_fallback_answer(
            payload.query,
            fallback_rows,
            failure_reason=provider_error,
        )
        sources = _build_rag_sources(fallback_rows)
    answer = _postprocess_grounded_answer(
        answer,
        context_text=context_text,
        grounded_url_allowlist=grounded_url_allowlist,
    )

    return RAGSummaryAnswer(answer=answer, sources=sources)

@router.post(
    "/reload-models",
    summary="Перезагрузить модели RAG",
    dependencies=[Depends(require_api_key)],
)
def rag_reload_models(db: Session = Depends(get_db_dep)) -> Dict[str, Any]:  # noqa: ARG001
    """
    Перезагрузить модели эмбеддингов и ранкинга в RAG-системе.
    Проксирует существующий метод rag_system.reload_models().
    """
    result = rag_system.reload_models()
    # Ожидается словарь с ключами 'embedding' и 'reranker' (bool)
    return {
        "embedding": bool(result.get("embedding")),
        "reranker": bool(result.get("reranker")),
    }
