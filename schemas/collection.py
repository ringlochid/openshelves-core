"""
Collection Pydantic schemas for Library Service.
Supports curated book collections with workflow, versioning, and ordered books.
"""
from uuid import UUID
from .shared import BookRead
from pydantic import BaseModel, Field
from .shared import BaseSchema, TimestampMixin, VersioningMixin, WorkflowMixin


# ========================================
# Create/Update Schemas
# ========================================

class CollectionCreate(BaseModel):
    """Schema for creating a new collection submission."""
    name: str = Field(..., min_length=1, max_length=200, description="Collection name")
    description: str | None = Field(None, description="Collection description")
    cover_key: str | None = Field(None, description="S3 key for collection cover image")


class CollectionUpdate(BaseModel):
    """Schema for updating an existing collection (versioned)."""
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    cover_key: str | None = None
    version: int = Field(..., description="Current version for optimistic locking")


class CollectionApproval(BaseModel):
    """Schema for admin approval/rejection."""
    is_public: bool = Field(..., description="Make collection publicly visible")
    version: int = Field(..., description="Current version for optimistic locking")


# ========================================
# Collection-Book Management
# ========================================

class CollectionBookAdd(BaseModel):
    """Schema for adding a book to a collection."""
    book_id: int = Field(..., description="Book to add")
    position: int = Field(..., ge=0, description="Position in collection (0-based)")


class CollectionBookUpdate(BaseModel):
    """Schema for updating book position in collection."""
    position: int = Field(..., ge=0, description="New position")


class CollectionBookRead(BaseSchema):
    """Book in collection with position info."""
    book_id: int
    position: int
    added_at: str  # datetime
    book: "BookRead"  # Full book details


# ========================================
# Response Schemas
# ========================================

class CollectionRead(BaseSchema, TimestampMixin):
    """Basic collection information for list views."""
    id: int
    name: str
    description: str | None
    cover_key: str | None
    status: str  # ContentStatus enum value
    is_public: bool
    subscriber_count: int
    created_by_user_id: UUID


class CollectionDetail(BaseSchema, TimestampMixin, VersioningMixin, WorkflowMixin):
    """Complete collection information including workflow metadata."""
    id: int
    name: str
    description: str | None
    cover_key: str | None
    created_by_user_id: UUID
    subscriber_count: int
    
    # Ordered books in collection
    books: list[CollectionBookRead] = Field(default_factory=list, description="Books in order")


# ========================================
# Subscription Schemas
# ========================================

class CollectionSubscriptionCreate(BaseModel):
    """Schema for subscribing to collection updates."""
    collection_id: int


class CollectionSubscriptionRead(BaseSchema):
    """Response schema for subscription relationship."""
    collection_id: int
    created_at: str  # datetime


# ========================================
# Pagination Response
# ========================================

class CollectionListResponse(BaseModel):
    """Paginated list of collections."""
    items: list[CollectionRead]
    total: int
    page: int
    per_page: int
    pages: int
