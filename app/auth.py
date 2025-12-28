"""
Authentication utilities for Portal
"""
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from functools import wraps
from typing import Optional
import os

from .models import PortalUser, get_session


def get_current_user(request: Request) -> Optional[dict]:
    """Get current user from session"""
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    
    db = get_session()
    try:
        user = db.query(PortalUser).filter(PortalUser.id == user_id).first()
        if user and user.is_active:
            return {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'is_admin': user.is_admin,
                'campuses': [{'id': c.id, 'campus_id': c.campus_id, 'name': c.name} for c in user.campuses]
            }
    finally:
        db.close()
    
    return None


def require_auth(func):
    """Decorator to require authentication"""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            return RedirectResponse(url="/auth/login", status_code=303)
        request.state.user = user
        return await func(request, *args, **kwargs)
    return wrapper


def require_admin(request: Request) -> dict:
    """Dependency to require admin access"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user.get('is_admin'):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def can_access_campus(request: Request, campus_id: int) -> bool:
    """Check if current user can access a campus"""
    user = get_current_user(request)
    if not user:
        return False
    if user.get('is_admin'):
        return True
    return any(c['campus_id'] == campus_id for c in user.get('campuses', []))
