"""
Admin Routes
"""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os

from .auth import get_current_user, require_admin
from .models import PortalUser, PortalCampus, get_session

router = APIRouter(prefix="/admin", tags=["Admin"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, user: dict = Depends(require_admin)):
    """List all portal users"""
    db = get_session()
    try:
        users = db.query(PortalUser).order_by(PortalUser.email).all()
        return templates.TemplateResponse("admin/users.html", {
            "request": request,
            "user": user,
            "users": users
        })
    finally:
        db.close()


@router.get("/users/new", response_class=HTMLResponse)
async def new_user_form(request: Request, user: dict = Depends(require_admin)):
    """New user form"""
    db = get_session()
    try:
        campuses = db.query(PortalCampus).filter(PortalCampus.active == True).order_by(PortalCampus.name).all()
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request,
            "user": user,
            "campuses": campuses,
            "edit_user": None
        })
    finally:
        db.close()


@router.post("/users/new")
async def create_user(
    request: Request,
    email: str = Form(...),
    name: str = Form(""),
    is_admin: bool = Form(False),
    campus_ids: list = Form(default=[]),
    user: dict = Depends(require_admin)
):
    """Create new user"""
    db = get_session()
    try:
        # Check if user exists
        existing = db.query(PortalUser).filter(PortalUser.email == email.lower()).first()
        if existing:
            request.session['flash'] = f"User {email} already exists"
            return RedirectResponse(url="/admin/users", status_code=303)
        
        new_user = PortalUser(
            email=email.lower().strip(),
            name=name,
            is_admin=is_admin
        )
        
        # Add campus assignments
        if campus_ids:
            campuses = db.query(PortalCampus).filter(PortalCampus.id.in_(campus_ids)).all()
            new_user.campuses = campuses
        
        db.add(new_user)
        db.commit()
        
        request.session['flash'] = f"User {email} created successfully"
        return RedirectResponse(url="/admin/users", status_code=303)
    finally:
        db.close()


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(request: Request, user_id: int, user: dict = Depends(require_admin)):
    """Edit user form"""
    db = get_session()
    try:
        edit_user = db.query(PortalUser).filter(PortalUser.id == user_id).first()
        if not edit_user:
            return RedirectResponse(url="/admin/users", status_code=303)
        
        campuses = db.query(PortalCampus).filter(PortalCampus.active == True).order_by(PortalCampus.name).all()
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request,
            "user": user,
            "campuses": campuses,
            "edit_user": edit_user
        })
    finally:
        db.close()


@router.post("/users/{user_id}/edit")
async def update_user(
    request: Request,
    user_id: int,
    email: str = Form(...),
    name: str = Form(""),
    is_admin: bool = Form(False),
    is_active: bool = Form(True),
    campus_ids: list = Form(default=[]),
    user: dict = Depends(require_admin)
):
    """Update user"""
    db = get_session()
    try:
        edit_user = db.query(PortalUser).filter(PortalUser.id == user_id).first()
        if not edit_user:
            return RedirectResponse(url="/admin/users", status_code=303)
        
        edit_user.email = email.lower().strip()
        edit_user.name = name
        edit_user.is_admin = is_admin
        edit_user.is_active = is_active
        
        # Update campus assignments
        if campus_ids:
            campuses = db.query(PortalCampus).filter(PortalCampus.id.in_(campus_ids)).all()
            edit_user.campuses = campuses
        else:
            edit_user.campuses = []
        
        db.commit()
        
        request.session['flash'] = f"User {email} updated successfully"
        return RedirectResponse(url="/admin/users", status_code=303)
    finally:
        db.close()


@router.post("/users/{user_id}/delete")
async def delete_user(request: Request, user_id: int, user: dict = Depends(require_admin)):
    """Delete user"""
    db = get_session()
    try:
        del_user = db.query(PortalUser).filter(PortalUser.id == user_id).first()
        if del_user and del_user.id != user['id']:  # Can't delete yourself
            db.delete(del_user)
            db.commit()
            request.session['flash'] = f"User deleted"
        return RedirectResponse(url="/admin/users", status_code=303)
    finally:
        db.close()


@router.get("/campuses", response_class=HTMLResponse)
async def list_campuses(request: Request, user: dict = Depends(require_admin)):
    """List all campuses"""
    db = get_session()
    try:
        campuses = db.query(PortalCampus).order_by(PortalCampus.name).all()
        return templates.TemplateResponse("admin/campuses.html", {
            "request": request,
            "user": user,
            "campuses": campuses
        })
    finally:
        db.close()


@router.post("/campuses/sync")
async def sync_campuses(request: Request, user: dict = Depends(require_admin)):
    """Sync campuses from mirror database"""
    try:
        from mirror_database import Campus, get_mirror_db
        
        mirror = get_mirror_db()
        mirror_campuses = mirror.session.query(Campus).all()
        
        db = get_session()
        added = 0
        updated = 0
        
        for mc in mirror_campuses:
            existing = db.query(PortalCampus).filter(PortalCampus.campus_id == mc.campus_id).first()
            if existing:
                if existing.name != mc.name:
                    existing.name = mc.name
                    updated += 1
            else:
                new_campus = PortalCampus(campus_id=mc.campus_id, name=mc.name)
                db.add(new_campus)
                added += 1
        
        db.commit()
        db.close()
        
        return JSONResponse({
            "success": True,
            "added": added,
            "updated": updated
        })
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)
