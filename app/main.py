# app/main.py
"""FastAPI application for YouTube Digest."""
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.routes import health_router, router as api_router
from app.config import get_settings
from app.models import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

# HTTP Basic Auth
security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """
    Verify HTTP Basic Auth credentials.

    If no password is configured, authentication is disabled.
    """
    if not settings.dashboard_password:
        return True  # No password = no auth required

    correct_username = secrets.compare_digest(
        credentials.username.encode("utf8"),
        settings.dashboard_username.encode("utf8"),
    )
    correct_password = secrets.compare_digest(
        credentials.password.encode("utf8"),
        settings.dashboard_password.encode("utf8"),
    )

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


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

# Include health router (no auth - for Docker/Kubernetes health checks)
app.include_router(health_router)

# Include API routes (with auth dependency for protected routes)
app.include_router(api_router, dependencies=[Depends(verify_credentials)])


# ============================================================================
# Static Files (robots.txt, llms.txt) - No Auth Required
# ============================================================================


@app.get("/robots.txt", tags=["Static"])
async def robots_txt():
    """Serve robots.txt to block crawlers."""
    return FileResponse(static_path / "robots.txt", media_type="text/plain")


@app.get("/llms.txt", tags=["Static"])
async def llms_txt():
    """Serve llms.txt to inform AI crawlers."""
    return FileResponse(static_path / "llms.txt", media_type="text/plain")


# ============================================================================
# Dashboard Route (Auth Protected)
# ============================================================================


@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard(request: Request, _auth: bool = Depends(verify_credentials)):
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
async def video_detail(
    request: Request, video_id: str, _auth: bool = Depends(verify_credentials)
):
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
