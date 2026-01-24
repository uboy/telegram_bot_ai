from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
import tempfile
import os

from backend.api.deps import get_db_dep, require_api_key
from backend.services.ingestion_service import IngestionService
from backend.services.indexing_service import IndexingService


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
    deleted_chunks: int
    pages_processed: int | None = None
    files_processed: int | None = None
    chunks_added: int
    wiki_root: str


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
        deleted_chunks=result.get("deleted_chunks", 0),
        pages_processed=result.get("pages_processed"),
        files_processed=None,
        chunks_added=result.get("chunks_added", 0),
        wiki_root=result.get("wiki_root", str(url)),
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
        deleted_chunks=result.get("deleted_chunks", 0),
        pages_processed=None,
        files_processed=result.get("files_processed"),
        chunks_added=result.get("chunks_added", 0),
        wiki_root=result.get("wiki_root", str(url)),
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
        deleted_chunks=result.get("deleted_chunks", 0),
        pages_processed=None,
        files_processed=result.get("files_processed"),
        chunks_added=result.get("chunks_added", 0),
        wiki_root=result.get("wiki_root", str(url)),
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


