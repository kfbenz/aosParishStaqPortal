"""
Dashboard Routes
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import sys

from .auth import get_current_user, require_auth
from .models import PortalCampus, get_session

# Add path for mirror database
sys.path.insert(0, '/opt/portal_app/aosParishStaq/src')

router = APIRouter(tags=["Dashboard"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/dashboard", response_class=HTMLResponse)
@require_auth
async def dashboard(request: Request):
    """Main dashboard"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    db = get_session()
    try:
        if user['is_admin']:
            campuses = db.query(PortalCampus).filter(PortalCampus.active == True).order_by(PortalCampus.name).all()
        else:
            # Get user's assigned campuses
            from .models import PortalUser
            db_user = db.query(PortalUser).filter(PortalUser.id == user['id']).first()
            campuses = db_user.campuses if db_user else []
        
        # Get stats from mirror database
        stats = {}
        try:
            from mirror_database import Individual, Family, get_mirror_db
            mirror = get_mirror_db()
            stats['total_individuals'] = mirror.session.query(Individual).count()
            stats['total_families'] = mirror.session.query(Family).count()
        except Exception as e:
            stats['error'] = str(e)
        
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "user": user,
            "campuses": campuses,
            "stats": stats
        })
    finally:
        db.close()
