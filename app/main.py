# app/main.py
"""FastAPI application for YouTube Digest."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.routes import router as api_router
from app.config import get_settings
from app.models import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting YouTube Digest application")

    # Initialize database tables
    init_db()
    logger.info("Database initialized")

    yield

    # Shutdown
    logger.info("Shutting down YouTube Digest application")


# Create FastAPI app
app = FastAPI(
    title="YouTube Digest",
    description="Automated YouTube subscription digest with AI summaries",
    version=__version__,
    lifespan=lifespan,
)

# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Setup templates
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=templates_path)

# Include API routes
app.include_router(api_router)


# ============================================================================
# Dashboard Route
# ============================================================================


@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard(request: Request):
    """
    Render the main dashboard page.
    """
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "YouTube Digest",
            "version": __version__,
        },
    )


@app.get("/video/{video_id}", response_class=HTMLResponse, tags=["Dashboard"])
async def video_detail(request: Request, video_id: str):
    """
    Render video detail page with full summary.
    """
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "YouTube Digest",
            "version": __version__,
            "video_id": video_id,
        },
    )


# ============================================================================
# Error Handlers
# ============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return HTMLResponse(
        content=f"<h1>Internal Server Error</h1><p>{str(exc)}</p>",
        status_code=500,
    )
