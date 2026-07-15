import logging

from fastapi import FastAPI, Response

from app.api.errors import register_exception_handlers
from app.api.v1.approval_requests import router as approval_requests_router
from app.config import get_settings
from app.db.session import ping
from app.observability.logging import configure_logging
from app.observability.middleware import RequestIdMiddleware

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, version="0.1.0")

    app.add_middleware(RequestIdMiddleware)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    async def ready(response: Response) -> dict[str, str]:
        if await ping():
            return {"status": "ok"}
        response.status_code = 503
        return {"status": "unavailable"}

    app.include_router(approval_requests_router)
    register_exception_handlers(app)

    logger.info("%s starting", settings.app_name, extra={"context": {"env": settings.env}})
    return app


app = create_app()
