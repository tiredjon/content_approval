from fastapi import FastAPI, Response

from app.api.v1.approval_requests import router as approval_requests_router
from app.config import get_settings
from app.db.session import ping


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")

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

    return app


app = create_app()
