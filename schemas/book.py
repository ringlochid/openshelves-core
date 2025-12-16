"""
Book Pydantic schemas for Library Service.
Supports workflow, versioning, file uploads, and subscription tracking.
"""
from enum import Enum
from uuid import UUID
from .shared import BookRead, AuthorRead, ReviewRead
from pydantic import BaseModel, Field
from .shared import BaseSchema, TimestampMixin, VersioningMixin, WorkflowMixin


# ========================================
# Enums for Sorting
# ========================================

class SortField(str, Enum):
    """Fields available for sorting books."""
    by_similarity = "similarity"
    by_title = "title"
    by_year = "year"
    by_rating = "rating"
    by_subscribers = "subscribers"


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
    tags: list[str] = Field(default_factory=list, description="Tags like ['fantasy', 'classic']")
    cover_key: str | None = Field(None, description="S3 key for cover image")
    file_key: str | None = Field(None, description="S3 key for book file (PDF/EPUB/MOBI)")
    file_format: str | None = Field(None, pattern="^(pdf|epub|mobi)$", description="File format")
    author_ids: list[int] = Field(default_factory=list, description="Authors to associate")


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


class BookApproval(BaseModel):
    """Schema for admin approval/rejection."""
    is_public: bool = Field(..., description="Make book publicly visible")
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
# Search/Filter Schemas
# ========================================

class BookSearchParams(BaseModel):
    """Search parameters for book queries."""
    q: str | None = Field(None, description="Full-text search query")
    tags: list[str] = Field(default_factory=list, description="Filter by tags (AND logic)")
    year_min: int | None = Field(None, gt=0)
    year_max: int | None = Field(None, gt=0)
    author_id: int | None = Field(None, description="Filter by author")
    status: str | None = Field(None, pattern="^(PENDING|APPROVED|REJECTED)$")
    is_public: bool | None = None


# ========================================
# Pagination Responses
# ========================================

class PaginatedBooksCursor(BaseModel):
    """Cursor-based pagination for infinite scroll."""
    items: list[BookListRead]
    next_cursor: str | None = None