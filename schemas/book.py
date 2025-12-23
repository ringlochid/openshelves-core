"""
Book Pydantic schemas for Library Service.
Supports workflow, versioning, file uploads, and subscription tracking.
"""

from enum import Enum
from uuid import UUID
from .shared import BookRead, AuthorRead, ReviewRead
from pydantic import BaseModel, Field
from .shared import (
    BaseSchema,
    TimestampMixin,
    VersioningMixin,
    WorkflowMixin,
    AuthorBrief,
    BookBrief,
)

# ========================================
# Enums for Sorting
# ========================================


class SortField(str, Enum):
    """Fields available for sorting books."""

    by_similarity = "similarity"
    by_title = "title"
    by_year = "year"
    by_average_rating = "average_rating"
    by_view_count = "view_count"
    by_trending_score = "trending_score"
    by_subscriber_count = "subscriber_count"


class SortDirection(str, Enum):
    """Sort order direction."""

    asc = "asc"
    desc = "desc"


class BookSortControl(BaseModel):
    """Sort control for book queries."""

    sort_field: SortField | None = None
    sort_direction: SortDirection | None = None


# ========================================
# Create/Update Schemas
# ========================================


class BookCreate(BaseModel):
    """Schema for creating a new book submission."""

    title: str = Field(..., min_length=1, max_length=500, description="Book title")
    year: int | None = Field(None, gt=0, description="Publication year")
    description: str | None = Field(None, description="Book description")
    tags: list[str] = Field(
        default_factory=list, description="Tags like ['fantasy', 'classic']"
    )
    cover_key: str | None = Field(None, description="S3 key for cover image")
    file_key: str | None = Field(
        None, description="S3 key for book file (PDF/EPUB/MOBI)"
    )
    file_format: str | None = Field(
        None, pattern="^(pdf|epub|mobi)$", description="File format"
    )
    author_ids: list[int] = Field(
        default_factory=list, description="Authors to associate"
    )


class BookReplace(BookCreate):
    """Schema for fully replacing a book PUT - versioned)."""

    version: int = Field(..., description="Current version for optimistic locking")


class BookUpdate(BaseModel):
    """Schema for updating an existing book (versioned)."""

    title: str | None = Field(None, min_length=1, max_length=500)
    year: int | None = Field(None, gt=0)
    description: str | None = None
    tags: list[str] | None = None
    cover_key: str | None = None
    file_key: str | None = None
    file_format: str | None = Field(None, pattern="^(pdf|epub|mobi)$")
    author_ids: list[int] | None = None
    version: int = Field(..., description="Current version for optimistic locking")


class RollbackRequest(BaseModel):
    """Schema for rolling back to a previous version."""

    target_version: int = Field(..., gt=0, description="Version to rollback to")
    version: int = Field(..., description="Current version for optimistic locking")


# ========================================
# Response Schemas
# ========================================
class BookListRead(BookRead):
    """Book with authors for list endpoints."""

    authors: list["AuthorRead"] = Field(default_factory=list)


class BookListItem(BookBrief):
    """Minimal book info for list views (optimized for listing cards)."""

    year: int | None
    authors: list[AuthorBrief] = []
    average_rating: float = 0.0
    view_count: int = 0
    trending_score: float = 0.0


class BookDetail(BaseSchema, TimestampMixin, VersioningMixin, WorkflowMixin):
    """Complete book information including workflow metadata."""

    id: int
    title: str
    year: int | None
    description: str | None
    tags: list[str] | None = Field(default=None)
    cover_key: str | None
    file_key: str | None
    file_format: str | None
    created_by_user_id: UUID
    subscriber_count: int
    average_rating: float = 0.0
    view_count: int = 0
    trending_score: float = 0.0

    # Relationships loaded separately
    authors: list["AuthorRead"] = Field(default_factory=list)
    reviews: list["ReviewRead"] = Field(default_factory=list)


# ========================================
# Subscription Schemas
# ========================================


class BookSubscriptionCreate(BaseModel):
    """Schema for subscribing to book updates."""

    book_id: int


class BookSubscriptionRead(BaseSchema):
    """Response schema for subscription relationship."""

    book_id: int
    created_at: str  # datetime


# ========================================
# Pagination Responses
# ========================================


class PaginatedBooksCursor(BaseModel):
    """Cursor-based pagination for infinite scroll."""

    items: list[BookListItem]
    next_cursor: str | None = None


class BookListResponse(BaseModel):
    """Offset-based paginated book list response."""

    items: list[BookListRead]
    total: int
    page: int
    per_page: int
    pages: int
