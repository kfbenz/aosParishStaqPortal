"""
Portal Configuration - All settings from environment variables
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # App settings
    app_name: str = "ParishStaq Portal"
    debug: bool = False
    
    # Security
    secret_key: str = Field(alias="SECRET_KEY")
    
    # Database
    database_url: str = Field(alias="PORTAL_DATABASE_URL")
    
    # Google Maps (for geocoding)
    google_maps_api_key: str = Field(default="", alias="GOOGLE_MAPS_API_KEY")
    
    # Email settings (for magic links)
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
