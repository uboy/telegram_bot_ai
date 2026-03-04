from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import logging
import os
import re

from backend.api.deps import get_db_dep, require_api_key
from backend.schemas.rag import RAGQuery, RAGAnswer, RAGSource, RAGSummaryQuery, RAGSummaryAnswer

# Используем существующую RAG-систему и AI-менеджер из основного проекта.
from shared.rag_system import rag_system  # type: ignore
from shared.ai_providers import ai_manager  # type: ignore
from shared.utils import create_prompt_with_language  # type: ignore
from shared.rag_safety import (  # type: ignore
    strip_unknown_citations,
    strip_untrusted_urls,
    sanitize_commands_in_answer,
)
from shared.logging_config import logger  # type: ignore
from shared.database import KnowledgeBase, KnowledgeChunk  # type: ignore
from shared.kb_settings import normalize_kb_settings  # type: ignore


router = APIRouter(prefix="/rag", tags=["rag"])


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

    try:
        # Настройки RAG
        try:
            from shared.config import (  # type: ignore
                RAG_TOP_K,
                RAG_CONTEXT_LENGTH,
                RAG_ENABLE_CITATIONS,
                RAG_MIN_RERANK_SCORE,
                RAG_DEBUG_RETURN_CHUNKS,
            )
            top_k_search = payload.top_k or RAG_TOP_K
            top_k_for_context = RAG_TOP_K
            context_length = RAG_CONTEXT_LENGTH
            enable_citations = RAG_ENABLE_CITATIONS
            min_rerank_score = RAG_MIN_RERANK_SCORE
            debug_return_chunks = RAG_DEBUG_RETURN_CHUNKS
        except Exception:  # noqa: BLE001
            top_k_search = payload.top_k or 10
            top_k_for_context = 8
            context_length = 1200
            enable_citations = True
            min_rerank_score = 0.0
            debug_return_chunks = False

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

        # Поиск кандидатов в RAG (dense + keyword + optional rerank)
        logger.debug("RAG query: query=%r, kb_id=%s, top_k=%s", payload.query, kb_id, top_k_search)
        results = rag_system.search(
            query=payload.query,
            knowledge_base_id=kb_id,
            top_k=top_k_search,
        ) or []
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

        def detect_intent(query: str) -> str:
            q = (query or "").lower()
            howto_terms = [
                "how to",
                "build",
                "run",
                "install",
                "setup",
                "compile",
                "unit test",
                "unittest",
                "tests",
            ]
            trouble_terms = [
                "error",
                "fail",
                "failed",
                "not working",
                "issue",
                "stacktrace",
            ]
            definition_terms = [
                "что такое",
                "как определяется",
                "как в документе определяется",
                "что называется",
                "что включает",
                "определение",
                "definition",
                "defined as",
                "what is",
            ]
            factoid_terms = [
                "кто",
                "какой",
                "какие",
                "какая",
                "сколько",
                "как часто",
                "какой целевой",
                "целевой показатель",
                "установлен на",
                "принимает решение",
                "механизмы реализации",
                "основные принципы",
                "в пункте",
                "до 2030 года",
                "what target",
                "who decides",
                "how often",
            ]
            if any(term in q for term in howto_terms):
                return "HOWTO"
            if any(term in q for term in trouble_terms):
                return "TROUBLE"
            if any(term in q for term in definition_terms):
                return "DEFINITION"
            if any(term in q for term in factoid_terms):
                return "FACTOID"
            return "GENERAL"

        def extract_query_hints(query: str) -> Dict[str, Any]:
            q = re.sub(r"\s+", " ", (query or "").lower()).strip()
            point_numbers = re.findall(r"пункт[а-я]*\s+(\d+)", q)
            definition_term = ""
            prefixes = (
                "как в документе определяется ",
                "как определяется ",
                "что такое ",
                "что называется ",
                "что включает ",
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
            fact_terms = []
            for token in re.findall(r"[а-яёa-z0-9]{3,}", q):
                if token in stop_words:
                    continue
                if token in fact_terms:
                    continue
                fact_terms.append(token)
            year_tokens = re.findall(r"\b(20\d{2})\b", q)
            return {
                "point_numbers": point_numbers,
                "definition_term": definition_term,
                "fact_terms": fact_terms[:10],
                "year_tokens": year_tokens,
            }

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

            if "unit test" in q or "unittest" in q or "tests" in q:
                if any(t in section_title for t in ("unit test", "unittest", "test")):
                    score += 2.0
                if any(t in doc_title for t in ("unit test", "unittest", "test")):
                    score += 1.0
                if any(t in text for t in ("unit test", "unittest")):
                    score += 1.0

            if intent == "HOWTO":
                if any(t in section_title for t in ("how to", "build", "run", "steps")):
                    score += 1.0
                if any(t in section_title for t in ("overview", "introduction")):
                    score -= 1.0
                # Prefer explicit Sync&Build docs for sync/build queries.
                if "sync" in q and "build" in q:
                    if "sync&build" in source_path or "sync&build" in doc_title or "sync&build" in section_title:
                        score += 4.0
                if "sync" in q:
                    if "sync" in source_path or "sync" in doc_title or "sync" in section_title:
                        score += 1.5
                if "build" in q:
                    if "build" in source_path or "build" in doc_title or "build" in section_title:
                        score += 0.5

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
            conditions = []
            if intent == "DEFINITION" and definition_term and len(definition_term) >= 3:
                conditions.append(KnowledgeChunk.content.ilike(f"%{definition_term}%"))
            if intent == "FACTOID":
                for term in fact_terms[:8]:
                    if len(term) >= 3:
                        conditions.append(KnowledgeChunk.content.ilike(f"%{term}%"))
                        conditions.append(KnowledgeChunk.chunk_metadata.ilike(f"%{term}%"))
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
                    if any(marker in text for marker in ("утвержда", "принима", "ежегод", "раз в", "показател", "экзафлопс", "ввп", "процент")):
                        fallback_score += 1.1
                    if re.search(r"\b\d+[.,]?\d*\b", text):
                        fallback_score += 0.8
                    for year in year_tokens:
                        if year in text:
                            fallback_score += 0.7

                for point_no in point_numbers:
                    if f"пункт {point_no}" in text:
                        fallback_score += 2.0
                    if re.search(rf"(?:^|\s){re.escape(point_no)}\.", text):
                        fallback_score += 2.5
                    if f"пункт {point_no}" in section_title or f"пункт {point_no}" in section_path:
                        fallback_score += 2.0
                    if re.search(rf"(?:^|\s){re.escape(point_no)}\.", section_title) or re.search(
                        rf"(?:^|\s){re.escape(point_no)}\.",
                        section_path,
                    ):
                        fallback_score += 1.2

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
            rows = (
                db.query(KnowledgeChunk)
                .filter(KnowledgeChunk.knowledge_base_id == kb_id)
                .filter(KnowledgeChunk.source_path == doc_id)
                .filter(KnowledgeChunk.is_deleted == False)
                .order_by(KnowledgeChunk.id.asc())
                .all()
            )
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
            def sort_key(item: dict) -> int:
                meta = item.get("metadata") or {}
                try:
                    return int(meta.get("chunk_no"))
                except (TypeError, ValueError):
                    return int(item.get("id") or 0)
            return sorted(chunks, key=sort_key)

        def build_context_block(
            r: dict,
            base_context_length: int,
            full_multiplier: int,
        ) -> Optional[str]:
            try:
                source_path = r.get("source_path") or ""
                meta = r.get("metadata") or {}
                title = meta.get("title") or source_path or "Без названия"

                doc_title = meta.get("doc_title") or meta.get("title") or ""
                section_path = meta.get("section_path") or ""
                section_title = meta.get("section_title") or ""
                chunk_kind = meta.get("chunk_kind") or "text"
                code_lang = meta.get("code_lang") or ""

                if source_path and ".keep" not in source_path.lower():
                    if "::" in source_path:
                        base_id = source_path.split("::")[-1]
                    elif "/" in source_path:
                        base_id = source_path.split("/")[-1]
                    else:
                        base_id = source_path
                    base_id = base_id.rsplit(".", 1)[0] if "." in base_id else base_id
                    source_id = f"{base_id}_{r.get('id')}" if r.get("id") else base_id
                else:
                    source_id = title.replace(" ", "_").lower()[:50]

                content = r.get("content") or ""

                if chunk_kind in ("code", "code_file"):
                    max_length = base_context_length * 3
                    if len(content) > max_length:
                        cut_point = content.rfind("\n", 0, max_length)
                        content_preview = content[:cut_point] if cut_point > max_length * 0.8 else content[:max_length]
                    else:
                        content_preview = content
                elif chunk_kind in ("full_page", "full_doc"):
                    max_length = base_context_length * max(1, full_multiplier)
                    if len(content) > max_length:
                        cut_point = content.rfind("\n", 0, max_length)
                        content_preview = content[:cut_point] + "..." if cut_point > max_length * 0.8 else content[:max_length] + "..."
                    else:
                        content_preview = content
                elif chunk_kind == "list":
                    max_length = base_context_length * 2
                    if len(content) > max_length:
                        cut_point = content.rfind("\n", 0, max_length)
                        content_preview = content[:cut_point] + "..." if cut_point > max_length * 0.8 else content[:max_length] + "..."
                    else:
                        content_preview = content
                else:
                    content_preview = content[:base_context_length]
                    if len(content) > base_context_length:
                        content_preview += "..."

                context_block_parts = []
                if enable_citations:
                    context_block_parts.append(f"SOURCE_ID: {source_id}")
                if doc_title:
                    context_block_parts.append(f"DOC: {doc_title}")
                if section_path:
                    context_block_parts.append(f"SECTION: {section_path}")
                if section_title and not section_path:
                    context_block_parts.append(f"SECTION: {section_title}")
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

        def build_context_blocks(
            top_chunk: dict,
            doc_chunks: List[dict],
            token_limit_chars: int,
        ) -> List[str]:
            if not doc_chunks:
                return []
            idx = 0
            for i, c in enumerate(doc_chunks):
                if c.get("id") == top_chunk.get("id"):
                    idx = i
                    break

            selected = [doc_chunks[idx]]
            if idx - 1 >= 0:
                selected.insert(0, doc_chunks[idx - 1])
            if idx + 1 < len(doc_chunks):
                selected.append(doc_chunks[idx + 1])

            for c in doc_chunks:
                title = (c.get("metadata") or {}).get("section_title") or ""
                title_lower = title.lower()
                if any(t in title_lower for t in ("prereq", "prerequisite", "setup")):
                    if c not in selected:
                        selected.insert(0, c)
                    break

            context_parts: List[str] = []
            total = 0
            for c in selected:
                block = build_context_block(c, context_length, full_page_multiplier)
                if not block:
                    continue
                if total + len(block) > token_limit_chars:
                    break
                context_parts.append(block)
                total += len(block)
            return context_parts

        intent = detect_intent(payload.query)
        query_hints = extract_query_hints(payload.query)

        if kb_id and (
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
            return RAGAnswer(answer="", sources=[])

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
            return RAGAnswer(answer="", sources=[])

        ranked_results: List[dict] = []
        for r in results:
            doc_id = get_doc_id(r)
            r["doc_id"] = doc_id
            r["rank_score"] = apply_boosts(payload.query, r, intent, query_hints)
            ranked_results.append(r)
        ranked_results.sort(key=lambda x: x.get("rank_score", 0.0), reverse=True)

        if single_page_mode and intent in {"DEFINITION", "FACTOID"}:
            top_k_for_context = max(top_k_for_context, min(8, len(ranked_results)))

        selected_docs = select_docs(intent, ranked_results)
        filtered_results = [r for r in ranked_results if r.get("doc_id") in selected_docs] or ranked_results
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("RAG intent=%s selected_docs=%s", intent, selected_docs)
            logger.debug("RAG filtered_results=%d", len(filtered_results))

        # Формируем контекст для LLM
        context_parts: list[str] = []
        if intent in {"HOWTO", "FACTOID"} and selected_docs:
            token_limit_chars = max(3000, context_length * 5)
            for doc_id in selected_docs:
                doc_chunks = load_doc_chunks(doc_id)
                if not doc_chunks:
                    continue
                top_chunk = next((r for r in filtered_results if r.get("doc_id") == doc_id), None)
                if not top_chunk:
                    continue
                if not top_chunk.get("id"):
                    for c in doc_chunks:
                        if c.get("content") == top_chunk.get("content"):
                            top_chunk["id"] = c.get("id")
                            break
                context_parts.extend(build_context_blocks(top_chunk, doc_chunks, token_limit_chars))
        else:
            for r in filtered_results[:top_k_for_context]:
                block = build_context_block(r, context_length, full_page_multiplier)
                if block:
                    context_parts.append(block)

        if not context_parts:
            logger.warning("No valid context parts extracted from results")
            return RAGAnswer(answer="", sources=[])

        context_text = "\n\n".join(context_parts)
        if logger.isEnabledFor(logging.DEBUG):
            source_ids = []
            for block in context_parts:
                for line in block.splitlines():
                    if line.startswith("SOURCE_ID:"):
                        source_ids.append(line.split(":", 1)[1].strip())
                        break
            logger.debug(
                "RAG context parts=%d, chars=%d, source_ids=%s",
                len(context_parts),
                len(context_text),
                source_ids,
            )
            if os.getenv("RAG_DEBUG_LOG_CONTEXT", "false").lower() == "true":
                preview_blocks = []
                for block in context_parts:
                    preview_blocks.append(block[:500])
                logger.debug("RAG context preview: %s", preview_blocks)

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
        ai_answer = strip_unknown_citations(ai_answer, context_text)
        ai_answer = strip_untrusted_urls(ai_answer, context_text)
        ai_answer = sanitize_commands_in_answer(ai_answer, context_text)
        logger.debug("AI manager returned answer length %d", len(ai_answer) if ai_answer else 0)
        
        # Возвращаем сырой markdown от LLM
        # Форматирование (clean_citations, format_commands_in_text, format_markdown_to_html)
        # будет выполнено в bot handler через format_for_telegram_answer()

        # Собираем список источников для ответа
        sources: list[RAGSource] = []
        for chunk in filtered_results:
            try:
                metadata = chunk.get("metadata") or {}
                sources.append(
                    RAGSource(
                        source_path=chunk.get("source_path") or metadata.get("source_path") or "",
                        source_type=chunk.get("source_type") or metadata.get("source_type") or "",
                        score=float(chunk.get("distance", 0.0)),
                    )
                )
            except (ValueError, TypeError) as e:
                logger.warning("Error processing source chunk: %s", e, exc_info=True)
                continue

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

        return RAGAnswer(answer=ai_answer, sources=sources, debug_chunks=debug_chunks)

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
        return RAGAnswer(answer="", sources=[])

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

    context_parts = []
    sources: List[RAGSource] = []
    seen = set()
    for r in results[:top_k]:
        content = r.get("content") or ""
        if not content:
            continue
        context_parts.append(content[:1500])
        sp = r.get("source_path") or ""
        st = r.get("source_type") or "unknown"
        key = (sp, st)
        if sp and key not in seen:
            seen.add(key)
            sources.append(RAGSource(source_path=sp, source_type=st, score=float(r.get("rerank_score") or 0.0)))

    context_text = "\n\n".join(context_parts)

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
    answer = strip_unknown_citations(answer, context_text)
    answer = strip_untrusted_urls(answer, context_text)
    answer = sanitize_commands_in_answer(answer, context_text)

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

