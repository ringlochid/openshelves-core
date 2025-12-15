"""
Application settings using Pydantic BaseSettings.
Centralized configuration for the Library Service.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Library Service Configuration."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # ============================
    # Database Configuration
    # ============================
    DATABASE_ASYNC_URL: str = "postgresql+asyncpg://postgres:123456@localhost:5432/library_app"
    
    # ============================
    # Redis Configuration
    # ============================
    REDIS_URL: str | None = None
    REDIS_HOST: str = "localhost"
    REDIS_PORT: str = "6379"
    REDIS_DB: str = "0"
    
    # Cache settings
    DEFAULT_CACHE_TTL: int = 300  # 5 minutes
    
    @property
    def redis_url(self) -> str:
        """Build Redis URL from components if REDIS_URL not provided."""
        if self.REDIS_URL:
            return self.REDIS_URL
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    # ============================
    # Auth Service Integration
    # ============================
    AUTH_SERVICE_URL: str = "http://auth-service:8000"
    SERVICE_API_KEY: str  # Required - shared secret for service-to-service auth
    JWT_PUBLIC_KEY_PATH: str = "keys/public_key.pem"
    
    # ============================
    # S3 Media Configuration
    # ============================
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "library-app-media"
    S3_ENDPOINT_URL: str | None = None  # For local testing with MinIO
    
    # Upload settings
    PRESIGNED_URL_EXPIRY: int = 600  # 10 minutes
    MAX_UPLOAD_SIZE_MB: int = 500  # 500MB max for book files
    
    # ============================
    # Celery Configuration
    # ============================
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None
    
    @property
    def celery_broker_url(self) -> str:
        """Celery broker URL (defaults to Redis)."""
        return self.CELERY_BROKER_URL or self.redis_url
    
    @property
    def celery_result_backend(self) -> str:
        """Celery result backend (defaults to Redis DB 1)."""
        if self.CELERY_RESULT_BACKEND:
            return self.CELERY_RESULT_BACKEND
        # Use DB 1 for results (safer than same DB as broker)
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/1"
    
    # ============================
    # ClamAV (Virus Scanning)
    # ============================
    CLAMAV_ENABLED: bool = False
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    
    # ============================
    # Content Management
    # ============================
    SOFT_DELETE_WINDOW_HOURS: int = 24  # Recovery window for soft-deleted content
    
    # ============================
    # API Configuration
    # ============================
    API_TITLE: str = "Library Service API"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "Wiki-style library management with RBAC and trust scoring"
    
    # CORS origins
    CORS_ORIGINS: list[str] = [
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]
    
    # ============================
    # Development Settings
    # ============================
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance.
    Use this function to get settings throughout the application.
    """
    return Settings()


# Global settings instance for convenience
settings = get_settings()
