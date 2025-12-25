"""
Author Pydantic schemas for Library Service.
Supports wiki-style author submissions with approval workflow and versioning.
"""

import re
from uuid import UUID
from .shared import AuthorRead
from pydantic import BaseModel, Field
from pydantic.functional_validators import field_validator
from pydantic.fields import computed_field
from .shared import (
    BaseSchema,
    TimestampMixin,
    VersioningMixin,
    WorkflowMixin,
    BookBrief,
    AvatarKeyMixin,
)

# ========================================
# Email Schema
# ========================================


class EmailSchema(BaseModel):
    email: str

    @field_validator("email")
    def validate_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        pattern = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
        if not pattern.match(cleaned):
            raise ValueError("Invalid email format")
        return cleaned


# ========================================
# Create/Update Schemas
# ========================================


class AuthorCreate(EmailSchema):
    """Schema for creating a new author submission.

    Note: avatar_key is set via upload pipeline only, not directly.
    """

    name: str = Field(..., min_length=1, max_length=100, description="Author full name")
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
    email: str | None = None
    bio: str | None = None
    linked_user_id: UUID | None = None
    book_ids: list[int] | None = None
    version: int = Field(..., description="Current version for optimistic locking")

    @field_validator("email")
    def validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        cleaned = v.strip().lower()
        pattern = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
        if not pattern.match(cleaned):
            raise ValueError("Invalid email format")
        return cleaned


class AuthorRollbackRequest(BaseModel):
    """Schema for rolling back to a previous version."""

    target_version: int = Field(..., ge=1, description="Version number to rollback to")
    version: int = Field(..., description="Current version for optimistic locking")


# ========================================
# Serialize Author Schemas
# ========================================


class AuthorSerialize(BaseSchema):
    """Schema for serializing author."""

    id: int
    version: int
    name: str = Field(..., min_length=1, max_length=100, description="Author full name")
    email: str | None = Field(None, description="Author email")
    bio: str | None = Field(None, description="Author biography")
    avatar_key: str | None = Field(None, description="Author avatar key")
    linked_user_id: UUID | None = Field(
        None, description="Link to registered user account"
    )

    # book relationships
    books: list["BookBrief"] = Field(default_factory=list)

    @computed_field
    @property
    def book_ids(self) -> list[int]:
        if not self.books:
            return []
        return [book.id for book in self.books]


# ========================================
# Response Schemas
# ========================================


class AuthorDetail(
    BaseSchema,
    TimestampMixin,
    VersioningMixin,
    WorkflowMixin,
    AvatarKeyMixin,
):
    """Complete author information including workflow metadata."""

    id: int
    name: str
    email: str | None
    bio: str | None
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
