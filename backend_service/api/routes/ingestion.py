from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
import tempfile
import os

from backend_service.api.deps import get_db_dep, require_api_key
from backend_service.services.ingestion_service import IngestionService


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


class ImageIngestionResponse(BaseModel):
    kb_id: int
    file_id: str
    source_path: str
    source_updated_at: str
    chunks_added: int


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

    service = IngestionService(db)
    result = service.ingest_web_page(
        kb_id=payload.knowledge_base_id,
        url=str(payload.url),
        telegram_id=payload.telegram_id,
        username=payload.username,
    )

    return WebIngestionResponse(**result)


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
    service = IngestionService(db)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = service.ingest_document_or_archive(
            kb_id=knowledge_base_id,
            file_path=tmp_path,
            file_name=file_name,
            file_type=file_type,
            telegram_id=telegram_id,
            username=username,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return DocumentIngestionResponse(
        mode=result.get("mode", "document"),
        kb_id=result.get("kb_id", knowledge_base_id),
        file_name=result.get("file_name", file_name),
        total_chunks=result.get("total_chunks", 0),
        doc_version=result.get("doc_version"),
        source_updated_at=result.get("source_updated_at"),
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
    service = IngestionService(db)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = service.ingest_image(
            kb_id=knowledge_base_id,
            file_path=tmp_path,
            file_id=file_id,
            telegram_id=telegram_id,
            username=username,
            model=model,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return ImageIngestionResponse(
        kb_id=result["kb_id"],
        file_id=result["file_id"],
        source_path=result["source_path"],
        source_updated_at=result["source_updated_at"],
        chunks_added=result["chunks_added"],
    )


