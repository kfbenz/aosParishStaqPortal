"""
Portal Database Models (MariaDB)
"""
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, 
    DateTime, ForeignKey, Table, Text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

Base = declarative_base()

# Association table for user-campus many-to-many
user_campuses = Table(
    'user_campuses',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('portal_users.id'), primary_key=True),
    Column('campus_id', Integer, ForeignKey('portal_campuses.id'), primary_key=True)
)


class PortalUser(Base):
    """Portal user accounts"""
    __tablename__ = 'portal_users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255))
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    
    # Many-to-many relationship with campuses
    campuses = relationship('PortalCampus', secondary=user_campuses, back_populates='users')
    
    def can_access_campus(self, campus_id: int) -> bool:
        """Check if user can access a specific campus"""
        if self.is_admin:
            return True
        return any(c.campus_id == campus_id for c in self.campuses)
    
    def __repr__(self):
        return f"<PortalUser(email='{self.email}', admin={self.is_admin})>"


class PortalCampus(Base):
    """Campus records for access control"""
    __tablename__ = 'portal_campuses'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    campus_id = Column(Integer, unique=True, nullable=False, index=True)  # ParishStaq campus ID
    name = Column(String(255), nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Many-to-many relationship with users
    users = relationship('PortalUser', secondary=user_campuses, back_populates='campuses')
    
    def __repr__(self):
        return f"<PortalCampus(name='{self.name}', campus_id={self.campus_id})>"


class ScanJob(Base):
    """Duplicate scan job tracking"""
    __tablename__ = 'scan_jobs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    campus_id = Column(Integer, index=True)
    campus_name = Column(String(255))
    status = Column(String(50), default='pending')  # pending, running, completed, failed
    scan_type = Column(String(50))  # quick, thorough, cross_campus
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    records_scanned = Column(Integer, default=0)
    duplicates_found = Column(Integer, default=0)
    results_summary = Column(Text)  # JSON string of results
    error_message = Column(String(500))
    created_by = Column(Integer, ForeignKey('portal_users.id'))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<ScanJob(id={self.id}, status='{self.status}')>"


# Database connection
_engine = None
_SessionLocal = None


def get_database_url():
    """Get database URL from environment"""
    url = os.environ.get('PORTAL_DATABASE_URL')
    if not url:
        raise ValueError("PORTAL_DATABASE_URL environment variable not set")
    return url


def get_engine():
    """Get or create database engine"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_database_url(),
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=5,
            max_overflow=10
        )
    return _engine


def get_session():
    """Get a new database session"""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def SessionLocal():
    """Alias for get_session for compatibility"""
    return get_session()


def init_db():
    """Initialize database tables"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("Portal database tables initialized")


def get_db():
    """Dependency for FastAPI routes"""
    db = get_session()
    try:
        yield db
    finally:
        db.close()
