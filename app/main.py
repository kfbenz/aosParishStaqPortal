"""
ParishStaq Portal - FastAPI Application
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import os

from .config import get_settings
from .models import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown"""
    print("> Initializing database...")
    init_db()
    print("> Portal ready!")
    yield
    print("> Shutting down...")


# Create app
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan
)

# Session middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="portal_session",
    max_age=86400  # 24 hours
)

# Mount static files
static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Templates
templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)

# Import and register routers
from .routes_auth import router as auth_router
from .routes_dashboard import router as dashboard_router
from .routes_admin import router as admin_router
from .routes_mirror import router as mirror_router
from .routes_duplicates import router as duplicates_router
from .routes_reports import router as reports_router
from .routes_geocoding import router as geocoding_router

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(mirror_router)
app.include_router(duplicates_router)
app.include_router(reports_router)
app.include_router(geocoding_router)


@app.get("/")
async def root():
    """Redirect root to dashboard"""
    return RedirectResponse(url="/dashboard", status_code=302)


# =============================================================================
# Error Handlers
# =============================================================================

@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    """Redirect to login on 401"""
    return RedirectResponse(url="/auth/login", status_code=302)


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    """Handle forbidden access"""
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Access Denied",
        "message": "You don't have permission to access this resource."
    }, status_code=403)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Handle 404 errors"""
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Not Found",
        "message": "The page you're looking for doesn't exist."
    }, status_code=404)
