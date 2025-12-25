"""
Collection Pydantic schemas for Library Service.
Supports curated book collections with workflow, versioning, and ordered books.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field
from pydantic.fields import computed_field
from .shared import (
    BaseSchema,
    TimestampMixin,
    VersioningMixin,
    WorkflowMixin,
    BookBrief,
    SortDirection,
)


# ========================================
# Sort Enums
# ========================================


class CollectionSortField(str, Enum):
    """Fields available for sorting collections."""

    by_name = "name"
    by_subscriber_count = "subscriber_count"
    by_trending_score = "trending_score"
    by_view_count = "view_count"
    by_similarity = "similarity"


class CollectionSortControl(BaseModel):
    """Sort control for collection queries."""

    sort_field: CollectionSortField | None = None
    sort_direction: SortDirection | None = None


# ========================================
# Create/Update Schemas
# ========================================


class CollectionCreate(BaseModel):
    """Schema for creating a new collection submission.

    Note: cover_key is set via upload pipeline only, not directly.
    """

    name: str = Field(..., min_length=1, max_length=200, description="Collection name")
    description: str | None = Field(None, description="Collection description")
    book_ids: list[int] = Field(
        default_factory=list,
        max_length=100,
        description="Book IDs to include (position = index + 1)",
    )


class CollectionUpdate(BaseModel):
    """Schema for updating an existing collection (versioned).

    Note: cover_key cannot be set directly - use upload endpoints.
    """

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    book_ids: list[int] | None = Field(
        None, max_length=100, description="Replace all books (position = index + 1)"
    )
    version: int = Field(..., description="Current version for optimistic locking")


class CollectionRollbackRequest(BaseModel):
    """Schema for rolling back to a previous version."""

    target_version: int = Field(..., ge=1, description="Version number to rollback to")
    version: int = Field(..., description="Current version for optimistic locking")


# ========================================
# Serialize Collection Schemas
# ========================================


class CollectionSerialize(BaseSchema):
    """Schema for serializing collection."""

    id: int
    version: int
    name: str = Field(..., min_length=1, max_length=100, description="Collection name")
    description: str | None = Field(None, description="Collection description")
    cover_key: str | None = Field(None, description="Collection cover key")

    # collection-book relationships
    books: list["CollectionBookRead"] = Field(default_factory=list)

    @computed_field
    @property
    def book_ids(self) -> list[int]:
        if not self.books:
            return []
        return [book.book_id for book in self.books]


# ========================================
# Collection-Book Management
# ========================================


class CollectionBookAdd(BaseModel):
    """Schema for adding a book to a collection."""

    book_id: int = Field(..., description="Book to add")
    position: int = Field(1, ge=1, description="Position (1-based, will be clamped)")


class CollectionBookUpdate(BaseModel):
    """Schema for updating book position in collection."""

    position: int = Field(..., ge=1, description="New position (1-based)")


class CollectionBookRead(BaseSchema):
    """Book in collection with position info."""

    book_id: int
    position: int
    added_at: datetime
    book: BookBrief  # Minimal book details for efficiency


# ========================================
# Response Schemas
# ========================================


class CollectionListItem(BaseSchema):
    """Minimal collection info for list views (optimized for cards)."""

    id: int
    name: str
    cover_key: str | None
    subscriber_count: int
    book_count: int
    status: str
    is_public: bool
    view_count: int = 0
    trending_score: float = 0.0


class CollectionRead(BaseSchema, TimestampMixin):
    """Basic collection information for list views."""

    id: int
    name: str
    description: str | None
    cover_key: str | None
    status: str  # ContentStatus enum value
    is_public: bool
    subscriber_count: int
    book_count: int = 0
    view_count: int = 0
    trending_score: float = 0.0
    created_by_user_id: UUID


class CollectionDetail(BaseSchema, TimestampMixin, VersioningMixin, WorkflowMixin):
    """Complete collection information including workflow metadata."""

    id: int
    name: str
    description: str | None
    cover_key: str | None
    created_by_user_id: UUID
    subscriber_count: int
    book_count: int = 0
    view_count: int = 0
    trending_score: float = 0.0

    # Ordered books in collection
    books: list[CollectionBookRead] = Field(
        default_factory=list, description="Books in order"
    )


# ========================================
# Action Response Schemas
# ========================================


class CollectionBookAddResponse(BaseModel):
    """Response for adding a book to collection."""

    message: str
    position: int
    book_count: int


class CollectionBookReorderResponse(BaseModel):
    """Response for reordering a book within collection."""

    message: str
    old_position: int
    new_position: int


class SubscriptionResponse(BaseModel):
    """Response for subscription action."""

    message: str
    subscriber_count: int


# ========================================
# Subscription Schemas
# ========================================


class CollectionSubscriptionRead(BaseSchema):
    """Response schema for subscription relationship."""

    collection_id: int
    created_at: str  # datetime


# ========================================
# Pagination Responses
# ========================================


class PaginatedCollectionsCursor(BaseModel):
    """Cursor-based pagination for infinite scroll."""

    items: list[CollectionListItem]
    next_cursor: str | None = None


class CollectionListResponse(BaseModel):
    """Offset-based paginated collection list response."""

    items: list[CollectionRead]
    total: int
    page: int
    per_page: int
    pages: int
