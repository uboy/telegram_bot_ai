from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any

from backend.api.deps import get_db_dep, require_api_key
from backend.schemas.rag import RAGQuery, RAGAnswer, RAGSource

# Используем существующую RAG-систему и AI-менеджер из основного проекта.
from shared.rag_system import rag_system  # type: ignore
from shared.ai_providers import ai_manager  # type: ignore
from shared.utils import create_prompt_with_language  # type: ignore
from shared.logging_config import logger  # type: ignore


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

        # Формируем контекст для LLM на основе найденных фрагментов
        context_parts: list[str] = []
        for idx, r in enumerate(results[:top_k_for_context], start=1):
            try:
                source_path = r.get("source_path") or ""
                meta = r.get("metadata") or {}
                title = meta.get("title") or source_path or "Без названия"
                
                # Извлекаем метаданные
                doc_title = meta.get("doc_title") or meta.get("title") or ""
                section_path = meta.get("section_path") or ""
                chunk_kind = meta.get("chunk_kind") or "text"
                code_lang = meta.get("code_lang") or ""

                # Формируем source_id для inline-citations
                if source_path and ".keep" not in source_path.lower():
                    if "::" in source_path:
                        source_id = source_path.split("::")[-1]
                    elif "/" in source_path:
                        source_id = source_path.split("/")[-1]
                    else:
                        source_id = source_path
                    source_id = source_id.rsplit(".", 1)[0] if "." in source_id else source_id
                else:
                    source_id = title.replace(" ", "_").lower()[:50]

                content = r.get("content") or ""
                
                # Правило обрезки в зависимости от chunk_kind
                if chunk_kind == "code":
                    # Для code-чанков не обрезаем или обрезаем очень мягко (context_length * 3)
                    max_length = context_length * 3
                    if len(content) > max_length:
                        # Обрезаем только если очень длинный, но стараемся не ломать команды
                        # Ищем последний перенос строки перед max_length
                        cut_point = content.rfind("\n", 0, max_length)
                        if cut_point > max_length * 0.8:  # Если нашли разумную точку обрезки
                            content_preview = content[:cut_point]
                        else:
                            # Если не нашли, обрезаем по max_length, но не добавляем "..."
                            content_preview = content[:max_length]
                    else:
                        content_preview = content
                elif chunk_kind == "list":
                    # Для списков обрезаем мягче (context_length * 2)
                    max_length = context_length * 2
                    if len(content) > max_length:
                        cut_point = content.rfind("\n", 0, max_length)
                        if cut_point > max_length * 0.8:
                            content_preview = content[:cut_point] + "..."
                        else:
                            content_preview = content[:max_length] + "..."
                    else:
                        content_preview = content
                else:
                    # Для обычного текста обрезаем как обычно
                    content_preview = content[:context_length]
                    if len(content) > context_length:
                        content_preview += "..."

                # Формируем блок контекста с метаданными
                context_block_parts = []
                
                if enable_citations:
                    context_block_parts.append(f"<source_id>{source_id}</source_id>")
                
                if doc_title:
                    context_block_parts.append(f"<doc_title>{doc_title}</doc_title>")
                
                if section_path:
                    context_block_parts.append(f"<section_path>{section_path}</section_path>")
                
                if chunk_kind:
                    context_block_parts.append(f"<chunk_kind>{chunk_kind}</chunk_kind>")
                
                if code_lang:
                    context_block_parts.append(f"<code_lang>{code_lang}</code_lang>")
                
                context_block_parts.append("<content>")
                context_block_parts.append(content_preview)
                context_block_parts.append("</content>")
                
                context_parts.append("\n".join(context_block_parts))
            except Exception as e:  # noqa: BLE001
                logger.warning("Error processing result %d: %s", idx, e, exc_info=True)
                continue

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

        logger.debug("Calling AI manager with prompt length %d", len(prompt))
        ai_answer = ai_manager.query(prompt)
        logger.debug("AI manager returned answer length %d", len(ai_answer) if ai_answer else 0)
        
        # Возвращаем сырой markdown от LLM
        # Форматирование (clean_citations, format_commands_in_text, format_markdown_to_html)
        # будет выполнено в bot handler через format_for_telegram_answer()

        # Собираем список источников для ответа
        sources: list[RAGSource] = []
        for chunk in results:
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
            for chunk in results[:5]:  # Первые 5 чанков
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

