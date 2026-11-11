from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings

app = FastAPI(
    title="Study Friend API",
    description="Study Friend backend API",
    version="0.1.0",
)

settings = get_settings()
frontend_origin = settings.frontend_url.rstrip("/")
frontend_host = urlsplit(frontend_origin).hostname
local_frontend = frontend_host in {"localhost", "127.0.0.1"}
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin] if frontend_origin else [],
    allow_origin_regex=(
        r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$"
        if local_frontend
        else None
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(api_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Study Friend API is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

