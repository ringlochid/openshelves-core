"""
Review Pydantic schemas for Library Service.
Supports review voting, trust rewards, and soft delete.
"""

from uuid import UUID
from pydantic import BaseModel, Field
from models import VoteType
from .shared import BaseSchema, TimestampMixin, ReviewRead


# ========================================
# Create/Update Schemas
# ========================================


class ReviewCreate(BaseModel):
    """Schema for creating a book review."""

    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    comment: str | None = Field(
        None, min_length=1, description="Review comment (no empty strings)"
    )


class ReviewUpdate(BaseModel):
    """Schema for updating a review."""

    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = Field(None, min_length=1)


# ========================================
# Response Schemas
# ========================================
class ReviewDetail(ReviewRead):
    """Complete review with user vote status."""

    user_vote: VoteType | None = Field(
        None, description="Current user's vote on this review"
    )


# ========================================
# Voting Schemas
# ========================================


class ReviewVoteCreate(BaseModel):
    """Schema for voting on review helpfulness."""

    vote: VoteType = Field(..., description="HELPFUL or UNHELPFUL")


class ReviewVoteUpdate(BaseModel):
    """Schema for changing a vote."""

    vote: VoteType


class ReviewVoteRead(BaseSchema):
    """Response schema for vote relationship."""

    review_id: int
    vote: VoteType
    created_at: str  # datetime


# ========================================
# Pagination Response
# ========================================


class ReviewListResponse(BaseModel):
    """Paginated list of reviews."""

    items: list[ReviewRead]
    total: int
    page: int
    per_page: int
    pages: int
