"""
Portal Configuration
Uses environment variables with sensible defaults
"""
import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # App
    app_name: str = "ParishStaq Portal"
    debug: bool = False
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    
    # Database
    database_url: str = "mysql+pymysql://portal:password@localhost/portal_app"
    
    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    
    # 2FA
    totp_issuer: str = "ParishStaq Portal"
    require_2fa: bool = True
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
