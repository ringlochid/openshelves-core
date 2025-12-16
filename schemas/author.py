"""
Author Pydantic schemas for Library Service.
Supports wiki-style author submissions with approval workflow and versioning.
"""
from uuid import UUID
from .shared import AuthorRead, BookRead
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from .shared import BaseSchema, TimestampMixin, VersioningMixin, WorkflowMixin


# ========================================
# Create/Update Schemas
# ========================================

class AuthorCreate(BaseModel):
    """Schema for creating a new author submission."""
    name: str = Field(..., min_length=1, max_length=100, description="Author full name")
    email: EmailStr | None = Field(None, description="Author contact email")
    bio: str | None = Field(None, description="Author biography")
    avatar_key: str | None = Field(None, description="S3 key for author avatar image")
    linked_user_id: UUID | None = Field(None, description="Link to registered user account")
    book_ids: list[int] = Field(default_factory=list, description="Books to associate with this author")


class AuthorUpdate(BaseModel):
    """Schema for updating an existing author (versioned)."""
    name: str | None = Field(None, min_length=1, max_length=100)
    email: EmailStr | None = None
    bio: str | None = None
    avatar_key: str | None = None
    linked_user_id: UUID | None = None
    book_ids: list[int] | None = None
    version: int = Field(..., description="Current version for optimistic locking")


class AuthorApproval(BaseModel):
    """Schema for admin approval/rejection."""
    is_public: bool = Field(..., description="Make author publicly visible")
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
    books: list["BookRead"] = Field(default_factory=list, description="Books by this author")


class AuthorWithBooks(AuthorRead):
    """Author with associated books (for list endpoints with ?include=books)."""
    books: list["BookRead"] = Field(default_factory=list)


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
    next_cursor: str | None = Field(None, description="Cursor for next page (null if last page)")