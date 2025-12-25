"""
Shared Pydantic schemas and mixins for Library Service.
Base classes and common field groups for reuse across entities.
"""

from pydantic.fields import computed_field
from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from models import ContentStatus
from settings import settings

# ========================================
# Shared Enums
# ========================================


class SortDirection(str, Enum):
    """Sort order direction for list queries."""

    asc = "asc"
    desc = "desc"


# ========================================
# Base Configuration
# ========================================


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(from_attributes=True)


# ========================================
# Mixins for Common Field Groups
# ========================================


class TimestampMixin(BaseModel):
    """Timestamp fields for all entities."""

    created_at: datetime
    updated_at: datetime


class WorkflowMixin(BaseModel):
    """Workflow fields for content requiring approval."""

    status: ContentStatus
    is_public: bool
    is_deleted: bool
    deleted_at: datetime | None = None
    vote_score: int = Field(default=0, ge=0, le=5, description="Jury vote score (0-5)")


class VersioningMixin(BaseModel):
    """Version control fields for optimistic locking."""

    version: int
    last_edited_by: UUID | None = None
    last_edited_at: datetime | None = None


# ========================================
# Media key shcemas
# ========================================
class CoverKeyMixin(BaseModel):
    """Mixin providing cover_key → cover_urls computed field."""

    cover_key: str | None = None

    @computed_field
    @property
    def cover_urls(self) -> dict[str, str] | None:
        """Generate S3 URLs for all cover size variants."""
        if not self.cover_key:
            return None
        if not settings.S3_BUCKET_NAME or not settings.AWS_REGION:
            return None

        # Support custom S3 endpoints (MinIO, LocalStack, CDN)
        if settings.S3_ENDPOINT_URL:
            base_url = (
                f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{settings.S3_BUCKET_NAME}"
            )
        else:
            base_url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com"
        parts = self.cover_key.rsplit("/", 1)
        if len(parts) != 2:
            return None

        base_path, filename = parts
        if "." not in filename:
            return None
        ext = filename.rsplit(".", 1)[1]

        # Cover sizes are (width, height) tuples
        return {
            f"{w}x{h}": f"{base_url}/{base_path}/{w}x{h}.{ext}"
            for w, h in settings.COVER_SIZES
        }


class AvatarKeyMixin(BaseModel):
    """Mixin providing avatar_key → avatar_urls computed field."""

    avatar_key: str | None = None

    @computed_field
    @property
    def avatar_urls(self) -> dict[str, str] | None:
        """Generate S3 URLs for all avatar size variants."""
        if not self.avatar_key:
            return None
        if not settings.S3_BUCKET_NAME or not settings.AWS_REGION:
            return None

        # Support custom S3 endpoints (MinIO, LocalStack, CDN)
        if settings.S3_ENDPOINT_URL:
            base_url = (
                f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{settings.S3_BUCKET_NAME}"
            )
        else:
            base_url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com"
        parts = self.avatar_key.rsplit("/", 1)
        if len(parts) != 2:
            return None

        base_path, filename = parts
        if "." not in filename:
            return None
        ext = filename.rsplit(".", 1)[1]

        # Avatar sizes are single integers (width)
        return {
            str(w): f"{base_url}/{base_path}/{w}.{ext}" for w in settings.AVATAR_SIZES
        }


class FileKeyMixin(BaseModel):
    """Mixin providing Filekey -> filekey url"""

    file_key: str | None = None

    @computed_field
    @property
    def file_url(self) -> str | None:
        """Generate S3 URL for file."""
        if not self.file_key:
            return None
        if not settings.S3_BUCKET_NAME or not settings.AWS_REGION:
            return None

        # Support custom S3 endpoints (MinIO, LocalStack, CDN)
        if settings.S3_ENDPOINT_URL:
            base_url = (
                f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{settings.S3_BUCKET_NAME}"
            )
        else:
            base_url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com"
        parts = self.file_key.rsplit("/", 1)
        if len(parts) != 2:
            return None

        base_path, filename = parts
        if "." not in filename:
            return None

        return f"{base_url}/{base_path}/{filename}"


# ========================================
# Core Schemas (to avoid circular imports)
# ========================================


class AuthorRead(BaseSchema, TimestampMixin):
    """Basic author information for list views."""

    id: int
    name: str
    email: str | None
    avatar_key: str | None
    status: str  # ContentStatus enum value
    is_public: bool
    follower_count: int
    created_by_user_id: UUID


class AuthorBrief(BaseSchema):
    """Minimal author info for book list cards."""

    id: int
    name: str


class BookRead(BaseSchema, TimestampMixin, CoverKeyMixin):
    """Basic book information for list views."""

    id: int
    title: str
    year: int | None
    description: str | None
    tags: list[str] | None = None
    subscriber_count: int
    created_by_user_id: UUID
    average_rating: float = 0.0
    view_count: int = 0
    trending_score: float = 0.0


class BookBrief(BaseSchema, CoverKeyMixin):
    """Minimal book info for author list cards."""

    id: int
    title: str
    subscriber_count: int
    status: str  # ContentStatus enum value
    is_public: bool
    average_rating: float = 0.0


class ReviewRead(BaseSchema, TimestampMixin):
    """Basic review information for list views."""

    id: int
    book_id: int
    user_id: UUID
    rating: int
    comment: str | None
    helpful_count: int
    unhelpful_count: int
    trust_awarded: int
    is_deleted: bool
