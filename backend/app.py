from fastapi import FastAPI

from backend.api.routes.health import router as health_router
from backend.api.routes.rag import router as rag_router
from backend.api.routes.users import router as users_router
from backend.api.routes.knowledge import router as knowledge_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.ingestion import router as ingestion_router
from backend.core.settings import settings


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

    return app


app = create_app()


