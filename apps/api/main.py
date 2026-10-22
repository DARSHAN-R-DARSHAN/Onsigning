"""
Onsigning Core FastAPI Application
Feature 1: Single-Agreement Playbook Audit
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.core.config import settings
from apps.api.core.logging import logger
from apps.api.core.exceptions import OnsigningException
from apps.api.core.database import engine, Base
from apps.api.routers import health, documents, audits, playbooks, organizations


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: initialize database schema for dev/test mode."""
    logger.info("Starting Onsigning API server...")
    # In SQLite or testing environments, auto-create tables if they don't exist
    if "sqlite" in settings.DATABASE_URL:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    logger.info("Shutting down Onsigning API server...")
    await engine.dispose()


app = FastAPI(
    title="Onsigning Contract Intelligence API",
    description="Evidence-backed post-signature contract execution and monitoring platform. Feature 1: Single-Agreement Playbook Audit.",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan
)

# CORS Configuration
origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(OnsigningException)
async def onsigning_exception_handler(request: Request, exc: OnsigningException):
    """Structured handler for typed domain exceptions."""
    logger.warning(
        f"Domain error {exc.status_code}: {exc.message}",
        extra={"error_code": exc.status_code, "path": request.url.path}
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict()
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Catch-all exception handler to prevent leaking raw internal exceptions,
    stack traces, or confidential contract contents.
    """
    logger.error(
        f"Unhandled internal server error on {request.method} {request.url.path}: {str(exc)}",
        extra={"path": request.url.path}
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected server error occurred. Diagnostics have been recorded safely.",
            "status_code": 500
        }
    )


# Mount Feature 1 Routers
app.include_router(health.router)
app.include_router(organizations.router)
app.include_router(documents.router)
app.include_router(audits.router)
app.include_router(playbooks.router)
