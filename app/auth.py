"""
Authentication Module with TOTP 2FA
"""
import bcrypt
import pyotp
import qrcode
import io
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from .models import User, get_db
from .config import get_settings

settings = get_settings()

# OAuth2 scheme for token extraction
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


# =============================================================================
# Password Functions
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    password_bytes = plain_password.encode('utf-8')
    hash_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hash_bytes)


# =============================================================================
# JWT Token Functions
# =============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.jwt_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None


# =============================================================================
# TOTP 2FA Functions
# =============================================================================

def generate_totp_secret() -> str:
    """Generate a new TOTP secret"""
    return pyotp.random_base32()


def get_totp_uri(username: str, secret: str) -> str:
    """Get the provisioning URI for authenticator apps"""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=settings.totp_issuer)


def generate_qr_code(uri: str) -> str:
    """Generate QR code as base64 image"""
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(uri)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    
    return base64.b64encode(buffer.getvalue()).decode()


def verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code"""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)  # Allow 1 step drift


# =============================================================================
# User Authentication
# =============================================================================

def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """Authenticate user by username and password"""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """Get user by username"""
    return db.query(User).filter(User.username == username).first()


# =============================================================================
# FastAPI Dependencies
# =============================================================================

async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    Get current authenticated user from JWT token.
    Checks both Authorization header and cookie.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Try cookie if no bearer token
    if not token:
        token = request.cookies.get("access_token")
    
    if not token:
        raise credentials_exception
    
    # Remove "Bearer " prefix if present
    if token.startswith("Bearer "):
        token = token[7:]
    
    payload = decode_token(token)
    if payload is None:
        raise credentials_exception
    
    username: str = payload.get("sub")
    if username is None:
        raise credentials_exception
    
    # Check if 2FA was completed
    if settings.require_2fa and not payload.get("2fa_verified", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="2FA verification required"
        )
    
    user = get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise credentials_exception
    
    return user


async def get_current_active_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Require admin user"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


# =============================================================================
# User Management
# =============================================================================

def create_user(
    db: Session,
    username: str,
    email: str,
    password: str,
    is_admin: bool = False,
    allowed_campuses: str = 'all'
) -> User:
    """Create a new user"""
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        is_admin=is_admin,
        allowed_campuses=allowed_campuses
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def setup_user_2fa(db: Session, user: User) -> tuple[str, str]:
    """
    Initialize 2FA for a user.
    Returns (secret, qr_code_base64)
    """
    secret = generate_totp_secret()
    user.totp_secret = secret
    db.commit()
    
    uri = get_totp_uri(user.username, secret)
    qr_code = generate_qr_code(uri)
    
    return secret, qr_code


def enable_user_2fa(db: Session, user: User, code: str) -> bool:
    """
    Enable 2FA after verifying the code.
    Returns True if successful.
    """
    if not user.totp_secret:
        return False
    
    if verify_totp(user.totp_secret, code):
        user.totp_enabled = True
        db.commit()
        return True
    
    return False
