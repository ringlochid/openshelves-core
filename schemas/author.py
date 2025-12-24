"""
Author Pydantic schemas for Library Service.
Supports wiki-style author submissions with approval workflow and versioning.
"""

from uuid import UUID
from .shared import AuthorRead
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from .shared import (
    BaseSchema,
    TimestampMixin,
    VersioningMixin,
    WorkflowMixin,
    BookBrief,
)


# ========================================
# Create/Update Schemas
# ========================================


class AuthorCreate(BaseModel):
    """Schema for creating a new author submission.

    Note: avatar_key is set via upload pipeline only, not directly.
    """

    name: str = Field(..., min_length=1, max_length=100, description="Author full name")
    email: EmailStr | None = Field(None, description="Author contact email")
    bio: str | None = Field(None, description="Author biography")
    linked_user_id: UUID | None = Field(
        None, description="Link to registered user account"
    )
    book_ids: list[int] = Field(
        default_factory=list, description="Books to associate with this author"
    )


class AuthorReplace(AuthorCreate):
    """Schema for fully replacing an author (PUT - versioned)."""

    version: int = Field(..., description="Current version for optimistic locking")


class AuthorUpdate(BaseModel):
    """Schema for updating an existing author (versioned).

    Note: avatar_key cannot be set directly - use upload endpoints.
    """

    name: str | None = Field(None, min_length=1, max_length=100)
    email: EmailStr | None = None
    bio: str | None = None
    linked_user_id: UUID | None = None
    book_ids: list[int] | None = None
    version: int = Field(..., description="Current version for optimistic locking")


class AuthorRollbackRequest(BaseModel):
    """Schema for rolling back to a previous version."""

    target_version: int = Field(..., ge=1, description="Version number to rollback to")
    version: int = Field(..., description="Current version for optimistic locking")


# ========================================
# Response Schemas
# ========================================


class AuthorDetail(BaseSchema, TimestampMixin, VersioningMixin, WorkflowMixin):
    """Complete author information including workflow metadata."""

    id: int
    name: str
    email: str | None
    bio: str | None
    avatar_key: str | None
    created_by_user_id: UUID
    linked_user_id: UUID | None
    follower_count: int

    # Relationships loaded separately
    books: list["BookBrief"] = Field(
        default_factory=list, description="Books by this author"
    )


class AuthorWithBooks(AuthorRead):
    """Author with associated books (for list endpoints with ?include=books)."""

    books: list["BookBrief"] = Field(default_factory=list)


# ========================================
# Pagination Response
# ========================================


class AuthorListResponse(BaseModel):
    """Paginated list of authors."""

    items: list[AuthorRead]
    total: int
    page: int
    per_page: int
    pages: int


class AuthorListCursorResponse(BaseModel):
    """Cursor-based paginated list with similarity search."""

    items: list[AuthorRead]
    next_cursor: str | None = Field(
        None, description="Cursor for next page (null if last page)"
    )
