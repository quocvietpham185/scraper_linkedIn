"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import linkedin_app_router, router
from app.config import settings
from app.utils.file_utils import ensure_directory
from app.utils.logger import setup_logging


setup_logging()

ensure_directory(settings.raw_data_dir)
ensure_directory(settings.output_data_dir)
ensure_directory(settings.state_path.parent)
ensure_directory(settings.session_storage_dir)

import asyncio
import sys
from app.services.comment_verifier import background_verification_worker
from app.services.group_status_service import background_group_status_worker
from app.services.scheduled_crawl_service import start_scheduled_crawl_worker

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI(
    title="LinkedIn Group Crawler API",
    version="1.0.0",
    description="FastAPI service to login, crawl LinkedIn groups, and expose top daily posts for n8n.",
)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_verification_worker())
    asyncio.create_task(background_group_status_worker())
    if settings.scheduled_crawl_enabled:
        start_scheduled_crawl_worker()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
    """Return validation errors in the app's common response envelope."""

    error_messages: list[str] = []
    for item in exc.errors():
        location = " -> ".join(str(part) for part in item.get("loc", []))
        message = item.get("msg", "Invalid request")
        error_messages.append(f"{location}: {message}")

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Invalid request body",
            "data": {"errors": error_messages},
        },
    )


app.include_router(router, prefix="/api")
app.include_router(linkedin_app_router, prefix="/api")
