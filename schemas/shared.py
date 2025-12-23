"""
Shared Pydantic schemas and mixins for Library Service.
Base classes and common field groups for reuse across entities.
"""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from models import ContentStatus


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
# Legacy Base Classes (backward compatibility)
# ========================================


class AuthorBase(BaseSchema):
    """Legacy author base - use AuthorRead instead."""

    id: int
    name: str
    email: str | None = None


class ReviewBase(BaseSchema):
    """Legacy review base - use ReviewRead instead."""

    id: int
    rating: int
    user_id: UUID  # Changed from reviewer_name
    comment: str | None = None


class BookBase(BaseSchema):
    """Legacy book base - use BookRead instead."""

    id: int
    title: str
    year: int | None


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


class BookRead(BaseSchema, TimestampMixin):
    """Basic book information for list views."""

    id: int
    title: str
    year: int | None
    description: str | None
    tags: list[str] = []
    cover_key: str | None
    status: str  # ContentStatus enum value
    is_public: bool
    subscriber_count: int
    created_by_user_id: UUID
    average_rating: float = 0.0
    view_count: int = 0
    trending_score: float = 0.0


class BookBrief(BaseSchema):
    """Minimal book info for author list cards."""

    id: int
    title: str
    cover_key: str | None
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
