"""
Jury voting schemas for Library Service.
Provides response schemas for jury vote operations.
"""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .shared import BaseSchema


# ========================================
# Vote Response Schemas
# ========================================


class JuryVoteResponse(BaseSchema):
    """Response schema for casting a jury vote."""

    message: str = Field(default="Vote cast successfully")
    vote_weight: int = Field(
        ..., description="Vote weight applied (1 for contributor, 5 for trusted)"
    )
    new_vote_score: int = Field(..., description="New total vote score after this vote")
    auto_approved: bool = Field(
        default=False,
        description="Whether content was auto-approved (reached threshold)",
    )


# ========================================
# Vote Status Schemas
# ========================================


class JuryVoterInfo(BaseSchema):
    """Individual voter information."""

    user_id: UUID
    vote_value: int = Field(..., description="Weight of this vote (1 or 5)")
    voted_at: datetime


class JuryVoteStatus(BaseSchema):
    """Vote status for a pending entity."""

    entity_type: str = Field(
        ..., description="Type of entity (author, book, collection)"
    )
    entity_id: int
    vote_score: int = Field(..., description="Current total vote score")
    threshold: int = Field(default=5, description="Score needed for auto-approval")
    votes_needed: int = Field(
        ..., description="Remaining points needed to reach threshold"
    )
    voter_count: int = Field(..., description="Number of users who voted")
    voters: list[JuryVoterInfo] = Field(
        default_factory=list, description="List of voters"
    )
    user_has_voted: bool = Field(
        default=False, description="Whether current user has voted"
    )
