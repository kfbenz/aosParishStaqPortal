"""
Authentication Routes
"""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timezone
import os

from .models import PortalUser, get_session
from .auth import get_current_user

router = APIRouter(prefix="/auth", tags=["Authentication"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Display login page"""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    
    flash = request.session.pop('flash', None)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "flash": flash
    })


@router.post("/login")
async def login(request: Request, email: str = Form(...)):
    """Process login - magic link style (simplified for now)"""
    email = email.lower().strip()
    
    db = get_session()
    try:
        user = db.query(PortalUser).filter(PortalUser.email == email).first()
        
        if user and user.is_active:
            user.last_login = datetime.now(timezone.utc)
            db.commit()
            request.session['user_id'] = user.id
            return RedirectResponse(url="/dashboard", status_code=303)
        
        request.session['flash'] = "Account not found or inactive. Contact administrator."
        return RedirectResponse(url="/auth/login", status_code=303)
    finally:
        db.close()


@router.get("/logout")
async def logout(request: Request):
    """Log out user"""
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
