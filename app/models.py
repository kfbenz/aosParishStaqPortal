"""
Database Models for Portal
"""
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import get_settings

Base = declarative_base()


class User(Base):
    """Portal user with 2FA support"""
    __tablename__ = 'portal_users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    
    # 2FA
    totp_secret = Column(String(32), nullable=True)  # Set when 2FA enabled
    totp_enabled = Column(Boolean, default=False)
    
    # Status
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    
    # Permissions (comma-separated campus IDs, or 'all')
    allowed_campuses = Column(Text, default='all')
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)
    
    def __repr__(self):
        return f"<User(username='{self.username}', email='{self.email}')>"
    
    def can_access_campus(self, campus_id: int) -> bool:
        """Check if user can access a specific campus"""
        if self.allowed_campuses == 'all':
            return True
        allowed = [int(c.strip()) for c in self.allowed_campuses.split(',') if c.strip().isdigit()]
        return campus_id in allowed


# Database connection
_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, echo=False)
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_db():
    """Create tables if they don't exist"""
    Base.metadata.create_all(get_engine())


def get_db():
    """Dependency for FastAPI - yields a database session"""
    db = get_session()
    try:
        yield db
    finally:
        db.close()
