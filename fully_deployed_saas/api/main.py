"""
Autonomous SRE Intelligence SaaS — FastAPI Application Entrypoint.
Scales to zero on Cloud Run / Render; fully multi-tenant with pgvector and pay-per-token LLMs.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add project root to sys.path with top priority
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) in sys.path:
    sys.path.remove(str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from api.routes.alert_routes import router as alert_router
from api.routes.auth_routes import router as auth_router
from api.routes.connector_routes import router as connector_router
from api.routes.incident_routes import router as incident_router
from api.routes.runbook_routes import router as runbook_router
from core.config import get_settings
from core.utils import get_logger, setup_logging
from database.connection import close_db_pool, fetch_one, init_db_pool

setup_logging()
logger = get_logger("saas_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan managing database pool startup and teardown."""
    logger.info("saas_startup_commencing")
    try:
        await init_db_pool()
        logger.info("saas_db_ready")
    except Exception as exc:
        logger.error("saas_startup_db_warning", error=str(exc))
    yield
    logger.info("saas_shutdown_commencing")
    await close_db_pool()
    logger.info("saas_shutdown_complete")


settings = get_settings()

app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="Multi-tenant autonomous SRE incident diagnosis and response platform.",
    lifespan=lifespan,
)

# CORS Configuration
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(alert_router, prefix="/api/v1")
app.include_router(incident_router, prefix="/api/v1")
app.include_router(connector_router, prefix="/api/v1")
app.include_router(runbook_router, prefix="/api/v1")


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    """Liveness & Readiness probe for Cloud Run / Render / Kubernetes."""
    db_status = "ok"
    try:
        row = await fetch_one("SELECT 1 AS alive")
        if not row:
            db_status = "degraded"
    except Exception:
        db_status = "unreachable"

    return {
        "status": "healthy" if db_status == "ok" else "degraded",
        "database": db_status,
        "environment": settings.app_env,
        "version": settings.api_version,
    }


@app.get("/dashboard", tags=["System"], response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    """Serve the Command Center SaaS UI."""
    html_path = BASE_DIR / "dashboard" / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard file not found</h1>", status_code=404)


@app.get("/", tags=["System"])
async def root():
    """Redirect root to the interactive SRE command center dashboard."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
