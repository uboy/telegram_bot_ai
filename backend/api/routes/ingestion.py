from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
import tempfile
import os

from backend.api.deps import get_db_dep, require_api_key
from backend.services.ingestion_service import IngestionService
from backend.services.indexing_service import IndexingService
from shared.rag_system import rag_system, EmbeddingModelMismatchError  # type: ignore


class WebIngestionRequest(BaseModel):
    knowledge_base_id: int
    url: HttpUrl
    telegram_id: str | None = None
    username: str | None = None


class WebIngestionResponse(BaseModel):
    kb_id: int
    url: str
    chunks_added: int
    doc_version: int
    source_updated_at: str
    job_id: int | None = None


class WikiIngestionResponse(BaseModel):
    status: str
    stage: str | None = None
    failure_reason: str | None = None
    failure_message: str | None = None
    recovery_options: list[str] = []
    deleted_chunks: int
    pages_processed: int | None = None
    files_processed: int | None = None
    chunks_added: int
    wiki_root: str
    crawl_mode: str | None = None
    git_fallback_attempted: bool = False


class DocumentIngestionResponse(BaseModel):
    mode: str
    kb_id: int
    file_name: str
    total_chunks: int
    doc_version: int | None = None
    source_updated_at: str | None = None
    job_id: int | None = None
    summary: str | None = None


class ImageIngestionResponse(BaseModel):
    kb_id: int
    file_id: str
    source_path: str
    source_updated_at: str
    chunks_added: int
    job_id: int | None = None


class CodePathIngestionRequest(BaseModel):
    knowledge_base_id: int
    path: str
    repo_label: str | None = None
    telegram_id: str | None = None
    username: str | None = None


class CodeGitIngestionRequest(BaseModel):
    knowledge_base_id: int
    git_url: str
    telegram_id: str | None = None
    username: str | None = None


class CodeIngestionResponse(BaseModel):
    kb_id: int
    root: str
    files_processed: int
    files_skipped: int
    files_updated: int
    chunks_added: int
    job_id: int | None = None


class ReindexDocumentRequest(BaseModel):
    document_id: int
    knowledge_base_id: int


class ReindexDocumentResponse(BaseModel):
    chunks_updated: int
    kb_id: int
    faiss_rebuild: str = "pending"


class FlushIndexResponse(BaseModel):
    rebuilt_kbs: list[int]


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post(
    "/web",
    response_model=WebIngestionResponse,
    summary="Загрузить одну веб-страницу в базу знаний",
    dependencies=[Depends(require_api_key)],
)
def ingest_web_page_endpoint(
    payload: WebIngestionRequest,
    db: Session = Depends(get_db_dep),
) -> WebIngestionResponse:
    if not payload.knowledge_base_id:
        raise HTTPException(status_code=400, detail="knowledge_base_id is required")

    indexing = IndexingService(db)
    job = indexing.create_job(stage="web")
    indexing.run_async(
        indexing.run_web_job,
        job.id,
        {
            "kb_id": payload.knowledge_base_id,
            "url": str(payload.url),
            "telegram_id": payload.telegram_id,
            "username": payload.username,
        },
    )
    return WebIngestionResponse(
        kb_id=payload.knowledge_base_id,
        url=str(payload.url),
        chunks_added=0,
        doc_version=0,
        source_updated_at="",
        job_id=job.id,
    )


@router.post(
    "/wiki-crawl",
    response_model=WikiIngestionResponse,
    summary="Рекурсивно обойти wiki-раздел и загрузить страницы в БЗ",
    dependencies=[Depends(require_api_key)],
)
def ingest_wiki_crawl(
    knowledge_base_id: int,
    url: HttpUrl,
    telegram_id: str | None = None,
    username: str | None = None,
    db: Session = Depends(get_db_dep),
) -> WikiIngestionResponse:
    service = IngestionService(db)
    result = service.ingest_wiki_crawl(
        kb_id=knowledge_base_id,
        wiki_url=str(url),
        telegram_id=telegram_id,
        username=username,
    )
    return WikiIngestionResponse(
        status=str(result.get("status", "success")),
        stage=result.get("stage"),
        failure_reason=result.get("failure_reason"),
        failure_message=result.get("failure_message"),
        recovery_options=list(result.get("recovery_options") or []),
        deleted_chunks=result.get("deleted_chunks", 0),
        pages_processed=result.get("pages_processed"),
        files_processed=None,
        chunks_added=result.get("chunks_added", 0),
        wiki_root=result.get("wiki_root", str(url)),
        crawl_mode=result.get("crawl_mode"),
        git_fallback_attempted=bool(result.get("git_fallback_attempted", False)),
    )


@router.post(
    "/wiki-git",
    response_model=WikiIngestionResponse,
    summary="Загрузить вики из Git-репозитория",
    dependencies=[Depends(require_api_key)],
)
def ingest_wiki_git(
    knowledge_base_id: int,
    url: HttpUrl,
    telegram_id: str | None = None,
    username: str | None = None,
    db: Session = Depends(get_db_dep),
) -> WikiIngestionResponse:
    service = IngestionService(db)
    result = service.ingest_wiki_git(
        kb_id=knowledge_base_id,
        wiki_url=str(url),
        telegram_id=telegram_id,
        username=username,
    )
    return WikiIngestionResponse(
        status=str(result.get("status", "success")),
        stage=result.get("stage", "git"),
        failure_reason=result.get("failure_reason"),
        failure_message=result.get("failure_message"),
        recovery_options=list(result.get("recovery_options") or []),
        deleted_chunks=result.get("deleted_chunks", 0),
        pages_processed=None,
        files_processed=result.get("files_processed"),
        chunks_added=result.get("chunks_added", 0),
        wiki_root=result.get("wiki_root", str(url)),
        crawl_mode="git",
        git_fallback_attempted=False,
    )


@router.post(
    "/wiki-zip",
    response_model=WikiIngestionResponse,
    summary="Загрузить вики из ZIP архива",
    dependencies=[Depends(require_api_key)],
)
def ingest_wiki_zip(
    knowledge_base_id: int = Form(...),
    url: HttpUrl = Form(...),
    telegram_id: str | None = Form(None),
    username: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db_dep),
) -> WikiIngestionResponse:
    service = IngestionService(db)

    # Сохраняем загруженный файл во временный ZIP
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        content = file.file.read()
        tmp.write(content)
        zip_path = tmp.name

    try:
        result = service.ingest_wiki_zip(
            kb_id=knowledge_base_id,
            wiki_url=str(url),
            zip_path=zip_path,
            telegram_id=telegram_id,
            username=username,
        )
    finally:
        try:
            os.unlink(zip_path)
        except OSError:
            pass

    return WikiIngestionResponse(
        status=str(result.get("status", "success")),
        stage=result.get("stage", "zip"),
        failure_reason=result.get("failure_reason"),
        failure_message=result.get("failure_message"),
        recovery_options=list(result.get("recovery_options") or []),
        deleted_chunks=result.get("deleted_chunks", 0),
        pages_processed=None,
        files_processed=result.get("files_processed"),
        chunks_added=result.get("chunks_added", 0),
        wiki_root=result.get("wiki_root", str(url)),
        crawl_mode="zip",
        git_fallback_attempted=False,
    )


@router.post(
    "/document",
    response_model=DocumentIngestionResponse,
    summary="Загрузить документ или архив (ZIP) в базу знаний",
    dependencies=[Depends(require_api_key)],
)
def ingest_document(
    knowledge_base_id: int = Form(...),
    file_name: str = Form(...),
    file_type: str | None = Form(None),
    telegram_id: str | None = Form(None),
    username: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db_dep),
) -> DocumentIngestionResponse:
    indexing = IndexingService(db)
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = tmp.name

    job = indexing.create_job(stage="document")
    indexing.run_async(
        indexing.run_document_job,
        job.id,
        {
            "kb_id": knowledge_base_id,
            "file_path": tmp_path,
            "file_name": file_name,
            "file_type": file_type,
            "telegram_id": telegram_id,
            "username": username,
        },
    )

    return DocumentIngestionResponse(
        mode="document",
        kb_id=knowledge_base_id,
        file_name=file_name,
        total_chunks=0,
        doc_version=None,
        source_updated_at=None,
        job_id=job.id,
        summary="Задача запущена, используйте /jobs/{id} для статуса",
    )


@router.post(
    "/image",
    response_model=ImageIngestionResponse,
    summary="Обработать изображение и добавить его в базу знаний",
    dependencies=[Depends(require_api_key)],
)
def ingest_image(
    knowledge_base_id: int = Form(...),
    file_id: str = Form(...),
    telegram_id: str | None = Form(None),
    username: str | None = Form(None),
    model: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db_dep),
) -> ImageIngestionResponse:
    indexing = IndexingService(db)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = tmp.name

    job = indexing.create_job(stage="image")
    indexing.run_async(
        indexing.run_image_job,
        job.id,
        {
            "kb_id": knowledge_base_id,
            "file_path": tmp_path,
            "file_id": file_id,
            "telegram_id": telegram_id,
            "username": username,
            "model": model,
        },
    )

    return ImageIngestionResponse(
        kb_id=knowledge_base_id,
        file_id=file_id,
        source_path="",
        source_updated_at="",
        chunks_added=0,
        job_id=job.id,
    )


@router.post(
    "/code-path",
    response_model=CodeIngestionResponse,
    summary="Индексировать локальную кодовую базу",
    dependencies=[Depends(require_api_key)],
)
def ingest_codebase_path(
    payload: CodePathIngestionRequest,
    db: Session = Depends(get_db_dep),
) -> CodeIngestionResponse:
    indexing = IndexingService(db)
    job = indexing.create_job(stage="code_path")
    indexing.run_async(
        lambda job_id, pl: indexing.run_code_job(job_id, pl, mode="path"),
        job.id,
        {
            "kb_id": payload.knowledge_base_id,
            "code_path": payload.path,
            "telegram_id": payload.telegram_id,
            "username": payload.username,
            "repo_label": payload.repo_label,
        },
    )
    return CodeIngestionResponse(
        kb_id=payload.knowledge_base_id,
        root=payload.path,
        files_processed=0,
        files_skipped=0,
        files_updated=0,
        chunks_added=0,
        job_id=job.id,
    )


@router.post(
    "/code-git",
    response_model=CodeIngestionResponse,
    summary="Индексировать кодовую базу из git",
    dependencies=[Depends(require_api_key)],
)
def ingest_codebase_git(
    payload: CodeGitIngestionRequest,
    db: Session = Depends(get_db_dep),
) -> CodeIngestionResponse:
    indexing = IndexingService(db)
    job = indexing.create_job(stage="code_git")
    indexing.run_async(
        lambda job_id, pl: indexing.run_code_job(job_id, pl, mode="git"),
        job.id,
        {
            "kb_id": payload.knowledge_base_id,
            "git_url": payload.git_url,
            "telegram_id": payload.telegram_id,
            "username": payload.username,
        },
    )
    return CodeIngestionResponse(
        kb_id=payload.knowledge_base_id,
        root=payload.git_url,
        files_processed=0,
        files_skipped=0,
        files_updated=0,
        chunks_added=0,
        job_id=job.id,
    )


@router.post(
    "/reindex-document",
    response_model=ReindexDocumentResponse,
    summary="Переиндексировать один документ (пересчитать эмбеддинги)",
    description="Per-document re-indexing (RAGIDX-001). Пересчитывает эмбеддинги для всех чанков документа и ставит KB в очередь на rebuild FAISS индекса.",
    dependencies=[Depends(require_api_key)],
)
def reindex_document_endpoint(
    payload: ReindexDocumentRequest,
    db: Session = Depends(get_db_dep),
) -> ReindexDocumentResponse:
    """Re-index a single document by recomputing embeddings."""
    try:
        result = rag_system.reindex_document(
            document_id=payload.document_id,
            knowledge_base_id=payload.knowledge_base_id,
            session=db,
        )
        
        if "error" in result:
            raise HTTPException(
                status_code=503,
                detail=f"Embeddings not available: {result['error']}"
            )
        
        if result["chunks_updated"] == 0:
            # Check if document exists
            from shared.database import Document
            doc = db.query(Document).filter_by(
                id=payload.document_id,
                knowledge_base_id=payload.knowledge_base_id
            ).first()
            if not doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document {payload.document_id} not found in KB {payload.knowledge_base_id}"
                )
        
        return ReindexDocumentResponse(
            chunks_updated=result["chunks_updated"],
            kb_id=result["kb_id"],
            faiss_rebuild="pending",
        )
    
    except EmbeddingModelMismatchError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e),
            headers={"X-Error-Code": "embedding_model_mismatch"},
        )


@router.post(
    "/flush-index",
    response_model=FlushIndexResponse,
    summary="Принудительно пересобрать FAISS индекс для pending KBs",
    description="Flush pending FAISS rebuilds (RAGIDX-001). Пересобирает FAISS индекс для всех KB в очереди pending rebuild.",
    dependencies=[Depends(require_api_key)],
)
def flush_index_endpoint(
    knowledge_base_id: int | None = None,
    db: Session = Depends(get_db_dep),
) -> FlushIndexResponse:
    """Flush pending FAISS rebuilds."""
    result = rag_system.flush_pending_rebuilds(knowledge_base_id=knowledge_base_id)
    return FlushIndexResponse(rebuilt_kbs=result["rebuilt_kbs"])


@router.get(
    "/pending-rebuilds",
    response_model=list[int],
    summary="Получить список KB в очереди на rebuild FAISS",
    dependencies=[Depends(require_api_key)],
)
def get_pending_rebuilds_endpoint() -> list[int]:
    """Get list of KBs pending FAISS rebuild."""
    return rag_system.get_pending_rebuild_kbs()


