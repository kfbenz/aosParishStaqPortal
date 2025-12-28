"""
Portal Configuration - All settings from environment variables
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # App settings
    app_name: str = "ParishStaq Portal"
    debug: bool = False
    
    # Security
    secret_key: str
    
    # Database
    database_url: str
    
    # Google Maps (for geocoding)
    google_maps_api_key: str = ""
    
    # Email settings (for magic links)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Map environment variable names
        env_prefix = ""
        fields = {
            "secret_key": {"env": "SECRET_KEY"},
            "database_url": {"env": "PORTAL_DATABASE_URL"},
            "google_maps_api_key": {"env": "GOOGLE_MAPS_API_KEY"},
            "smtp_host": {"env": "SMTP_HOST"},
            "smtp_port": {"env": "SMTP_PORT"},
            "smtp_user": {"env": "SMTP_USER"},
            "smtp_password": {"env": "SMTP_PASSWORD"},
            "smtp_from": {"env": "SMTP_FROM"},
        }


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
