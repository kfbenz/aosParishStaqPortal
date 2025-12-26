"""
Admin Routes - User Management
"""
from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional

from .models import User, get_db
from .auth import get_current_active_admin, hash_password, setup_user_2fa

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory="templates")


# =============================================================================
# User Management
# =============================================================================

@router.get("/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    current_user: User = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    """List all users"""
    users = db.query(User).order_by(User.username).all()
    
    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "user": current_user,
        "users": users
    })


@router.get("/users/new", response_class=HTMLResponse)
async def new_user_form(
    request: Request,
    current_user: User = Depends(get_current_active_admin)
):
    """New user form"""
    return templates.TemplateResponse("admin/user_form.html", {
        "request": request,
        "user": current_user,
        "edit_user": None,
        "action": "Create"
    })


@router.post("/users/new", response_class=HTMLResponse)
async def create_user(
    request: Request,
    current_user: User = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    """Create new user"""
    form = await request.form()
    username = form.get("username", "").strip()
    email = form.get("email", "").strip()
    password = form.get("password", "")
    is_admin = form.get("is_admin") == "on"
    allowed_campuses = form.get("allowed_campuses", "all").strip()
    
    errors = []
    
    # Validate
    if not username:
        errors.append("Username is required")
    if not email:
        errors.append("Email is required")
    if not password:
        errors.append("Password is required")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    
    # Check for existing user
    if db.query(User).filter(User.username == username).first():
        errors.append(f"Username '{username}' already exists")
    if db.query(User).filter(User.email == email).first():
        errors.append(f"Email '{email}' already exists")
    
    if errors:
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request,
            "user": current_user,
            "edit_user": None,
            "action": "Create",
            "errors": errors,
            "form_data": {"username": username, "email": email, "allowed_campuses": allowed_campuses, "is_admin": is_admin}
        })
    
    # Create user
    new_user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        is_admin=is_admin,
        allowed_campuses=allowed_campuses or "all"
    )
    db.add(new_user)
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    """Edit user form"""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return templates.TemplateResponse("admin/user_form.html", {
        "request": request,
        "user": current_user,
        "edit_user": edit_user,
        "action": "Update"
    })


@router.post("/users/{user_id}/edit", response_class=HTMLResponse)
async def update_user(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    """Update user"""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    form = await request.form()
    email = form.get("email", "").strip()
    password = form.get("password", "")
    is_admin = form.get("is_admin") == "on"
    is_active = form.get("is_active") == "on"
    allowed_campuses = form.get("allowed_campuses", "all").strip()
    
    errors = []
    
    if not email:
        errors.append("Email is required")
    
    # Check for duplicate email
    existing = db.query(User).filter(User.email == email, User.id != user_id).first()
    if existing:
        errors.append(f"Email '{email}' is already in use")
    
    if password and len(password) < 8:
        errors.append("Password must be at least 8 characters")
    
    if errors:
        return templates.TemplateResponse("admin/user_form.html", {
            "request": request,
            "user": current_user,
            "edit_user": edit_user,
            "action": "Update",
            "errors": errors
        })
    
    # Update user
    edit_user.email = email
    edit_user.is_admin = is_admin
    edit_user.is_active = is_active
    edit_user.allowed_campuses = allowed_campuses or "all"
    
    if password:
        edit_user.hashed_password = hash_password(password)
    
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/reset-2fa")
async def reset_user_2fa(
    user_id: int,
    current_user: User = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    """Reset user's 2FA"""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    edit_user.totp_secret = None
    edit_user.totp_enabled = False
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    """Delete user"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    db.delete(edit_user)
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(
    user_id: int,
    current_user: User = Depends(get_current_active_admin),
    db: Session = Depends(get_db)
):
    """Toggle user active status"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    edit_user.is_active = not edit_user.is_active
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
