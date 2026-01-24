from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.routes.health import router as health_router
from backend.api.routes.rag import router as rag_router
from backend.api.routes.users import router as users_router
from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.ingestion import router as ingestion_router
from backend.api.routes.jobs import router as jobs_router
from backend.core.settings import settings
from shared.logging_config import logger  # type: ignore


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME)

    # Базовый префикс API v1
    prefix = settings.API_V1_PREFIX
    app.include_router(health_router, prefix=prefix)
    app.include_router(rag_router, prefix=prefix)
    app.include_router(users_router, prefix=prefix)
    app.include_router(knowledge_router, prefix=prefix)
    app.include_router(auth_router, prefix=prefix)
    app.include_router(ingestion_router, prefix=prefix)
    app.include_router(jobs_router, prefix=prefix)

    # Глобальный обработчик исключений
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Обработчик всех необработанных исключений."""
        logger.error(
            "Unhandled exception in %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal server error",
                "detail": "An unexpected error occurred",
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Обработчик HTTP исключений."""
        logger.warning(
            "HTTP exception in %s %s: %s - %s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.detail,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Обработчик ошибок валидации запросов."""
        logger.warning(
            "Validation error in %s %s: %s",
            request.method,
            request.url.path,
            exc.errors(),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "Validation error", "detail": exc.errors()},
        )

    return app


app = create_app()


