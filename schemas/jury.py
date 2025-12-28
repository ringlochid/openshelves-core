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
    """Vote status response in /vote endpoint."""

    has_voted: bool = Field(..., description="Whether user has voted")
    vote_weight: int = Field(..., description="Vote weight applied (1 or 5)")
    total_votes: int = Field(..., description="Total votes cast")
    voters: list[str] = Field(..., description="List of voters")
    vote_breakdown: dict[str, int] = Field(
        default_factory=dict, description="Breakdown of votes by user"
    )
    votes_needed: int = Field(..., description="Votes needed to reach threshold")
    threshold: int = Field(..., description="Threshold for auto-approval")
