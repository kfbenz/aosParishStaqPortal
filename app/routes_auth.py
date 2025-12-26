"""
Authentication Routes
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from .models import User, get_db
from .auth import (
    authenticate_user, create_access_token, verify_totp,
    setup_user_2fa, enable_user_2fa, get_current_user,
    hash_password, get_user_by_username
)
from .config import get_settings

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="templates")
settings = get_settings()


# =============================================================================
# Pydantic Models
# =============================================================================

class Token(BaseModel):
    access_token: str
    token_type: str
    requires_2fa: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class TwoFactorRequest(BaseModel):
    code: str


class Setup2FAResponse(BaseModel):
    qr_code: str  # Base64 encoded PNG
    secret: str   # For manual entry


# =============================================================================
# API Routes
# =============================================================================

@router.post("/token", response_model=Token)
async def login_for_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """OAuth2 compatible token endpoint"""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if 2FA is required
    if user.totp_enabled:
        # Issue temporary token that requires 2FA completion
        token = create_access_token(
            data={"sub": user.username, "2fa_verified": False}
        )
        return Token(access_token=token, token_type="bearer", requires_2fa=True)
    
    # No 2FA - issue full token
    token = create_access_token(
        data={"sub": user.username, "2fa_verified": True}
    )
    
    # Update last login
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    
    return Token(access_token=token, token_type="bearer", requires_2fa=False)


@router.post("/verify-2fa", response_model=Token)
async def verify_2fa(
    request: TwoFactorRequest,
    token: str = Depends(get_current_user),  # This will fail since 2FA not verified
    db: Session = Depends(get_db)
):
    """Verify 2FA code and get full access token"""
    # We need to handle this differently since get_current_user checks 2FA
    # This endpoint needs the partial token
    pass  # See the web routes below for the actual implementation


@router.post("/api/setup-2fa", response_model=Setup2FAResponse)
async def api_setup_2fa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Initialize 2FA setup - returns QR code (API endpoint)"""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled"
        )
    
    secret, qr_code = setup_user_2fa(db, current_user)
    return Setup2FAResponse(qr_code=qr_code, secret=secret)


@router.post("/api/enable-2fa")
async def api_enable_2fa(
    request: TwoFactorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Enable 2FA after verifying the code (API endpoint)"""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled"
        )
    
    if enable_user_2fa(db, current_user, request.code):
        return {"message": "2FA enabled successfully"}
    
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid 2FA code"
    )


# =============================================================================
# Web Routes (HTML)
# =============================================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle login form submission"""
    form = await request.form()
    username = form.get("username")
    password = form.get("password")
    
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid username or password"
        })
    
    # Check if 2FA is required
    if user.totp_enabled:
        # Create partial token and redirect to 2FA page
        token = create_access_token(
            data={"sub": user.username, "2fa_verified": False}
        )
        response = RedirectResponse(url="/auth/2fa", status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            key="partial_token",
            value=token,
            httponly=True,
            max_age=300  # 5 minutes to complete 2FA
        )
        return response
    
    # No 2FA - create full token and redirect to dashboard
    token = create_access_token(
        data={"sub": user.username, "2fa_verified": True}
    )
    
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        max_age=settings.jwt_expire_minutes * 60
    )
    return response


@router.get("/2fa", response_class=HTMLResponse)
async def two_factor_page(request: Request):
    """2FA verification page"""
    partial_token = request.cookies.get("partial_token")
    if not partial_token:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    
    return templates.TemplateResponse("2fa.html", {"request": request})


@router.post("/2fa", response_class=HTMLResponse)
async def two_factor_submit(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle 2FA form submission"""
    from .auth import decode_token
    
    partial_token = request.cookies.get("partial_token")
    if not partial_token:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    
    # Decode partial token to get username
    payload = decode_token(partial_token)
    if not payload:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    
    username = payload.get("sub")
    user = get_user_by_username(db, username)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    
    form = await request.form()
    code = form.get("code", "").strip()
    
    if not verify_totp(user.totp_secret, code):
        return templates.TemplateResponse("2fa.html", {
            "request": request,
            "error": "Invalid code. Please try again."
        })
    
    # 2FA verified - create full token
    token = create_access_token(
        data={"sub": user.username, "2fa_verified": True}
    )
    
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("partial_token")
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        max_age=settings.jwt_expire_minutes * 60
    )
    return response


@router.get("/logout")
async def logout():
    """Logout - clear cookies"""
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("partial_token")
    return response


@router.get("/setup-2fa", response_class=HTMLResponse)
async def setup_2fa_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """2FA setup page"""
    if current_user.totp_enabled:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    
    secret, qr_code = setup_user_2fa(db, current_user)
    
    return templates.TemplateResponse("setup_2fa.html", {
        "request": request,
        "qr_code": qr_code,
        "secret": secret
    })


@router.post("/setup-2fa", response_class=HTMLResponse)
async def setup_2fa_submit(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Handle 2FA setup form submission"""
    form = await request.form()
    code = form.get("code", "").strip()
    
    if enable_user_2fa(db, current_user, code):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    
    # Failed - regenerate QR code
    secret, qr_code = setup_user_2fa(db, current_user)
    
    return templates.TemplateResponse("setup_2fa.html", {
        "request": request,
        "qr_code": qr_code,
        "secret": secret,
        "error": "Invalid code. Please try again."
    })
