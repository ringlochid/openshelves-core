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

    # Database Configuration
    DATABASE_ASYNC_URL: str = "postgresql+asyncpg://postgres:123456@db:5432/library_app"
    DATABASE_SYNC_URL: str = (
        "postgresql+psycopg://postgres:123456@localhost:5432/library_app"  # For Alembic
    )

    # Redis Configuration
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

    # Auth Service Integration
    AUTH_SERVICE_URL: str = "http://auth-service:8000"
    SERVICE_API_KEY: str
    JWT_PUBLIC_KEY_PATH: str = "keys/public_key.pem"
    JWT_ALGORITHM: str = "RS256"
    JWT_ISSUER: str = "auth-service"
    JWT_AUDIENCE: str = "backend-services"
    AUTH_SERVICE_TIMEOUT: int = 10  # seconds

    # S3 Media Configuration
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_REGION: str = "ap-southeast-2"
    S3_BUCKET_NAME: str = "library-media-demo"
    S3_ENDPOINT_URL: str | None = None

    # Upload settings
    PRESIGNED_URL_EXPIRY: int = 600  # 10 minutes
    MAX_UPLOAD_SIZE_MB: int = 20

    # Celery Configuration
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

    # ClamAV (Virus Scanning) - set CLAMAV_HOST to enable scanning
    CLAMAV_HOST: str = ""  # Empty = disabled, set to hostname to enable
    CLAMAV_PORT: int = 3310

    # Content Management
    SOFT_DELETE_WINDOW_HOURS: int = 24  # Recovery window for soft-deleted content

    # API Configuration
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

    # Rate Limiting (Token Bucket)
    # Read endpoints (GET) - generous limits
    RATE_LIMIT_READ_CAPACITY: int = 50
    RATE_LIMIT_READ_REFILL_TOKENS: int = 50
    RATE_LIMIT_READ_REFILL_PERIOD_SECONDS: int = 120

    # Write endpoints (POST/PUT/PATCH) - moderate limits
    RATE_LIMIT_WRITE_CAPACITY: int = 30
    RATE_LIMIT_WRITE_REFILL_TOKENS: int = 30
    RATE_LIMIT_WRITE_REFILL_PERIOD_SECONDS: int = 3600

    # Sensitive endpoints (create content, jury voting) - strict limits
    RATE_LIMIT_SENSITIVE_CAPACITY: int = 10
    RATE_LIMIT_SENSITIVE_REFILL_TOKENS: int = 10
    RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS: int = 3600

    # Anonymous/IP-based rate limiting (unauthenticated requests)
    RATE_LIMIT_ANON_CAPACITY: int = 20
    RATE_LIMIT_ANON_REFILL_TOKENS: int = 20
    RATE_LIMIT_ANON_REFILL_PERIOD_SECONDS: int = 180

    # Upload rate limiting
    RATE_LIMIT_UPLOAD_CAPACITY: int = 10
    RATE_LIMIT_UPLOAD_REFILL_TOKENS: int = 10
    RATE_LIMIT_UPLOAD_REFILL_PERIOD_SECONDS: int = 3600  # 10/hour

    RATE_LIMIT_COMMIT_CAPACITY: int = 10
    RATE_LIMIT_COMMIT_REFILL_TOKENS: int = 10
    RATE_LIMIT_COMMIT_REFILL_PERIOD_SECONDS: int = 3600

    # Media Processing Configuration
    # Cover sizes (book & collection) - 2:3 portrait ratio
    COVER_SIZES: list[tuple[int, int]] = [(1800, 2700), (1200, 1800), (600, 900)]
    # Avatar sizes (author) - 1:1 square ratio
    AVATAR_SIZES: list[int] = [512, 256, 128]

    # Image processing
    COVER_OUTPUT_FORMAT: str = "WEBP"
    COVER_JPEG_QUALITY: int = 85
    COVER_UPSCALE_MAX: float = 5.0  # Max upscale factor
    COVER_MAX_PIXELS: int = 100_000_000  # Image bomb protection

    # Upload limits
    COVER_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB
    BOOK_FILE_MAX_BYTES: int = 100 * 1024 * 1024  # 100MB
    UPLOAD_EXPIRES_SECONDS: int = 600  # 10 min presigned URL expiry

    # Allowed MIME types
    COVER_ALLOWED_MIME_TYPES: list[str] = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/avif",
    ]
    BOOK_ALLOWED_FORMATS: list[str] = ["pdf", "epub"]

    # Development Settings
    LOG_LEVEL: str = "INFO"


@lru_cache()
def get_settings() -> Settings:
    """
    Use this function to get settings throughout the application.
    """
    return Settings()


# Global settings instance
settings = get_settings()
