"""
ParishStaq Geocoding Database Models (MariaDB)

Permanent database for storing geocoded addresses.
This database is separate from the mirror database to protect
geocoding data from being lost during mirror rebuilds.

Author: Kevin Benz - Archdiocese of Seattle IT
Date: December 2024
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, 
    DateTime, Text, Index, Boolean, func
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()


class GeocodeCache(Base):
    """
    Permanent cache of geocoded addresses.
    One entry per unique normalized address.
    
    This table stores:
    - Original address components
    - Normalized address key for matching
    - Geocoding results (coordinates, accuracy)
    - Full API response for audit trail
    - Usage statistics
    """
    __tablename__ = 'geocode_cache'
    
    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Cache key (normalized address for matching)
    # Format: "123 MAIN ST|SEATTLE|WA|98101"
    address_key = Column(String(500), unique=True, index=True, nullable=False)
    
    # Original address components
    street = Column(String(255))
    city = Column(String(100))
    state = Column(String(50))
    zip_code = Column(String(20))
    
    # Geocoding results
    latitude = Column(Float, index=True)
    longitude = Column(Float, index=True)
    formatted_address = Column(String(500))  # Provider's standardized version
    place_id = Column(String(255))           # Google Place ID (unique identifier)
    
    # Quality metrics
    # ROOFTOP: Most precise, address-level
    # RANGE_INTERPOLATED: Between two addresses
    # GEOMETRIC_CENTER: Center of a region
    # APPROXIMATE: General area only
    accuracy = Column(String(50))
    location_type = Column(String(50))       # Full location_type from API
    
    # Confidence score (calculated)
    # high: ROOFTOP
    # medium: RANGE_INTERPOLATED, GEOMETRIC_CENTER
    # low: APPROXIMATE
    confidence = Column(String(20))
    
    # API metadata
    geocode_provider = Column(String(50), default='google_maps')
    geocoded_at = Column(DateTime)
    api_response_json = Column(Text)         # Full JSON response for audit
    
    # Error tracking
    error_message = Column(String(500))
    retry_count = Column(Integer, default=0)
    last_error_at = Column(DateTime)
    
    # Usage tracking
    usage_count = Column(Integer, default=1)  # How many times this address was looked up
    last_used_at = Column(DateTime)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    
    # Indexes for common queries
    __table_args__ = (
        Index('ix_geocode_cache_city_state', 'city', 'state'),
        Index('ix_geocode_cache_zip', 'zip_code'),
        Index('ix_geocode_cache_coords', 'latitude', 'longitude'),
        Index('ix_geocode_cache_accuracy', 'accuracy'),
        {
            'mysql_engine': 'InnoDB',
            'mysql_charset': 'utf8mb4',
            'mysql_collate': 'utf8mb4_unicode_ci'
        }
    )
    
    def __repr__(self):
        return f"<GeocodeCache(id={self.id}, address='{self.street}, {self.city}', lat={self.latitude}, lng={self.longitude})>"
    
    @property
    def is_valid(self):
        """Check if this cache entry has valid coordinates"""
        return self.latitude is not None and self.longitude is not None
    
    @property
    def full_address(self):
        """Return the full address as a string"""
        parts = [self.street, self.city, self.state, self.zip_code]
        return ', '.join(p for p in parts if p)
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'address_key': self.address_key,
            'street': self.street,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'formatted_address': self.formatted_address,
            'place_id': self.place_id,
            'accuracy': self.accuracy,
            'confidence': self.confidence,
            'provider': self.geocode_provider,
            'geocoded_at': self.geocoded_at.isoformat() if self.geocoded_at else None,
            'usage_count': self.usage_count,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# =============================================================================
# DATABASE CONNECTION
# =============================================================================

_engine = None
_Session = None


def get_database_url():
    """
    Get MariaDB connection URL from environment.
    
    Uses PORTAL_DATABASE_URL by default (same database as Portal).
    Override with GEOCODING_DATABASE_URL if you want a separate database.
    """
    # Check for dedicated geocoding database URL first
    url = os.environ.get('GEOCODING_DATABASE_URL')
    if url:
        return url
    
    # Fall back to portal database URL (recommended - same DB, just adds geocode_cache table)
    portal_url = os.environ.get('PORTAL_DATABASE_URL')
    if portal_url:
        return portal_url
    
    raise ValueError(
        "No database URL configured. Set PORTAL_DATABASE_URL or GEOCODING_DATABASE_URL "
        "environment variable."
    )


def get_geocode_engine():
    """Get or create the geocoding database engine"""
    global _engine
    
    if _engine is None:
        database_url = get_database_url()
        
        _engine = create_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,  # Recycle connections after 1 hour
            pool_size=5,
            max_overflow=10
        )
    
    return _engine


def get_geocode_session():
    """Get a new database session for geocoding operations"""
    global _Session
    
    if _Session is None:
        engine = get_geocode_engine()
        _Session = sessionmaker(bind=engine)
    
    return _Session()


def init_geocoding_db():
    """Initialize the geocoding database (create tables)"""
    engine = get_geocode_engine()
    Base.metadata.create_all(engine)
    
    # Mask password in output
    url = get_database_url()
    if '@' in url:
        display_url = url.split('@')[1]
    else:
        display_url = 'configured'
    
    print(f"Geocoding database initialized (MariaDB)")
    print(f"Connection: {display_url}")
    return engine


def check_connection():
    """Test database connection"""
    try:
        engine = get_geocode_engine()
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        print("Database connection successful!")
        return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False


# =============================================================================
# CLI INTERFACE
# =============================================================================

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Geocoding Database Management')
    parser.add_argument('command', choices=['init', 'stats', 'export', 'check'])
    parser.add_argument('--output', '-o', help='Output file for export')
    
    args = parser.parse_args()
    
    if args.command == 'init':
        init_geocoding_db()
        print("Database initialized successfully")
    
    elif args.command == 'check':
        check_connection()
        
    elif args.command == 'stats':
        from sqlalchemy import func as sa_func
        
        session = get_geocode_session()
        total = session.query(GeocodeCache).count()
        
        accuracy_stats = session.query(
            GeocodeCache.accuracy,
            sa_func.count(GeocodeCache.id)
        ).group_by(GeocodeCache.accuracy).all()
        
        print(f"\nGeocoding Cache Statistics")
        print(f"=" * 40)
        print(f"Total cached addresses: {total:,}")
        print(f"\nBy accuracy:")
        for acc, count in accuracy_stats:
            print(f"  {acc or 'Unknown'}: {count:,}")
        
        total_usage = session.query(sa_func.sum(GeocodeCache.usage_count)).scalar() or 0
        print(f"\nTotal lookups: {total_usage:,}")
        print(f"API calls saved: {total_usage - total:,}")
        
        session.close()
        
    elif args.command == 'export':
        import json
        
        session = get_geocode_session()
        entries = session.query(GeocodeCache).all()
        
        data = [e.to_dict() for e in entries]
        
        output_file = args.output or 'geocoding_cache_export.json'
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"Exported {len(data)} entries to {output_file}")
        session.close()
