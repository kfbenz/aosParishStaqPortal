"""
ParishStaq Portal - FastAPI Application
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
from .config import get_settings
from .models import init_db
from .routes_auth import router as auth_router
from .routes_dashboard import router as dashboard_router
from .routes_admin import router as admin_router
from .routes_mirror import router as mirror_router
from .routes_duplicates import router as duplicates_router
from .routes_geocoding import router as geocoding_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown"""
    # Startup
    print("> Initializing database...")
    init_db()
    print("> Portal ready!")
    yield
    # Shutdown
    print("> Shutting down...")


# Create app
settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(mirror_router)
app.include_router(duplicates_router)
app.include_router(geocoding_router)


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
    templates = Jinja2Templates(directory="templates")
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Access Denied",
        "message": "You don't have permission to access this resource."
    }, status_code=403)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Handle 404 errors"""
    templates = Jinja2Templates(directory="templates")
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Not Found",
        "message": "The page you're looking for doesn't exist."
    }, status_code=404)
