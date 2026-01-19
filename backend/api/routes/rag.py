from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
import json

from backend.api.deps import get_db_dep, require_api_key
from backend.schemas.rag import RAGQuery, RAGAnswer, RAGSource

# Используем существующую RAG-систему и AI-менеджер из основного проекта.
from shared.rag_system import rag_system  # type: ignore
from shared.ai_providers import ai_manager  # type: ignore
from shared.utils import create_prompt_with_language  # type: ignore
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

        if not results:
            # Нет релевантных фрагментов в БЗ – честно говорим, что ответа нет
            return RAGAnswer(answer="", sources=[])

        # Анти-галлюцинации: если есть reranker и все score ниже порога – считаем, что ответа нет
        try:
            best_score = max(float(r.get("rerank_score", 0.0)) for r in results)
            if min_rerank_score > 0.0 and best_score < min_rerank_score:
                logger.debug("Best rerank score %f below threshold %f", best_score, min_rerank_score)
                return RAGAnswer(answer="", sources=[])
        except (ValueError, TypeError) as e:
            logger.warning("Error calculating best rerank score: %s", e, exc_info=True)
            # Продолжаем обработку, если не удалось вычислить best_score

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
            if any(term in q for term in howto_terms):
                return "HOWTO"
            if any(term in q for term in trouble_terms):
                return "TROUBLE"
            return "GENERAL"

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

        def apply_boosts(query: str, result: dict, intent: str) -> float:
            score = base_score(result)
            meta = result.get("metadata") or {}
            doc_title = (meta.get("doc_title") or meta.get("title") or "").lower()
            section_title = (meta.get("section_title") or "").lower()
            text = (result.get("content") or "").lower()
            q = (query or "").lower()

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

            if intent != "HOWTO":
                return [d for d, _ in items[:3]]

            if second_doc and (top_score - float(second_score)) < 0.3:
                return [top_doc, second_doc]
            return [top_doc]

        def load_doc_chunks(doc_id: str) -> List[dict]:
            rows = (
                db.query(KnowledgeChunk)
                .filter(KnowledgeChunk.knowledge_base_id == kb_id)
                .filter(KnowledgeChunk.source_path == doc_id)
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
        ranked_results: List[dict] = []
        for r in results:
            doc_id = get_doc_id(r)
            r["doc_id"] = doc_id
            r["rank_score"] = apply_boosts(payload.query, r, intent)
            ranked_results.append(r)
        ranked_results.sort(key=lambda x: x.get("rank_score", 0.0), reverse=True)

        selected_docs = select_docs(intent, ranked_results)
        filtered_results = [r for r in ranked_results if r.get("doc_id") in selected_docs] or ranked_results

        # Формируем контекст для LLM
        context_parts: list[str] = []
        if intent == "HOWTO" and selected_docs:
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

        # Вызываем LLM через общий ai_manager
        logger.debug("Creating prompt for LLM query")
        prompt = create_prompt_with_language(
            payload.query,
            context_text,
            task="answer",
            enable_citations=enable_citations,
        )

        def sanitize_commands_in_answer(answer: str, context: str) -> str:
            import re

            if not answer or not context:
                return answer

            context_norm = re.sub(r"\s+", " ", context).lower()
            command_prefixes = (
                "git ",
                "repo ",
                "./",
                "bash ",
                "python ",
                "pip ",
                "cmake ",
                "make ",
                "ninja ",
                "docker ",
                "kubectl ",
                "sudo ",
                "apt ",
                "yum ",
                "npm ",
                "yarn ",
            )

            def is_command_line(line: str) -> bool:
                s = line.strip()
                if not s:
                    return False
                if s.startswith("$ "):
                    s = s[2:].lstrip()
                if s.startswith(command_prefixes):
                    return True
                if " && " in s or s.startswith("cd "):
                    return True
                return False

            def line_in_context(line: str) -> bool:
                s = re.sub(r"\s+", " ", line.strip()).lower()
                return bool(s) and s in context_norm

            def contains_wiki_url(line: str) -> bool:
                return "/wikis/" in line or "#sync" in line or "#build" in line

            # Sanitize fenced code blocks
            code_pattern = r"```([a-zA-Z0-9+_-]*)\n(.*?)```"
            removed_any = False

            def replace_code(match):
                nonlocal removed_any
                lang = match.group(1) or ""
                body = match.group(2) or ""
                lines = body.splitlines()
                kept = []
                for ln in lines:
                    if is_command_line(ln):
                        if contains_wiki_url(ln) or not line_in_context(ln):
                            removed_any = True
                            continue
                    kept.append(ln)
                if not kept:
                    return "Команда отсутствует в базе знаний."
                return f"```{lang}\n" + "\n".join(kept) + "\n```"

            answer = re.sub(code_pattern, replace_code, answer, flags=re.DOTALL)

            # Sanitize inline code
            def replace_inline(match):
                nonlocal removed_any
                code = match.group(1) or ""
                if is_command_line(code):
                    if contains_wiki_url(code) or not line_in_context(code):
                        removed_any = True
                        return "команда отсутствует в базе знаний"
                return match.group(0)

            answer = re.sub(r"`([^`]+)`", replace_inline, answer)
            if removed_any and len(answer.strip()) < 80:
                return (
                    "В найденных источниках нет точных команд для сборки/запуска по вашему запросу. "
                    "Уточните компонент или платформу (например, C-API, XTS, e2e)."
                )
            return answer

        logger.debug("Calling AI manager with prompt length %d", len(prompt))
        ai_answer = ai_manager.query(prompt)
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
        # Пробрасываем HTTP исключения как есть
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Error in RAG query endpoint: query=%r, kb_id=%s, error=%s",
            payload.query,
            kb_id,
            e,
            exc_info=True,
        )
        # Возвращаем пустой ответ вместо 500, чтобы бот мог корректно обработать ситуацию
        return RAGAnswer(answer="", sources=[])


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

