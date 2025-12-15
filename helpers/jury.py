"""
Jury voting system helpers for democratic content approval.

The jury system allows community members to vote on PENDING content:
- Contributors (trust >= 10) with jury:vote: +1 vote
- Trusted users (trust >= 50) with jury:vote_weighted: +5 votes

When vote_score >= 5, content is automatically published to APPROVED.
"""
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from models import JuryVote, Author, Book, Collection, ContentStatus
from helpers.edit_history import serialize_entity, record_approval
from services.auth_client import adjust_trust_for_approval


# Vote threshold for auto-publish
VOTE_THRESHOLD = 5


def calculate_vote_weight(user_scopes: list[str]) -> int:
    """
    Calculate vote weight based on user scopes.
    
    Args:
        user_scopes: List of user scopes from JWT
        
    Returns:
        5 if user has jury:vote_weighted (trusted users)
        1 if user has jury:vote (contributors)
        0 if user has neither scope
    """
    if "jury:vote_weighted" in user_scopes:
        return 5
    elif "jury:vote" in user_scopes:
        return 1
    else:
        return 0


async def cast_jury_vote(
    db: AsyncSession,
    user_id: UUID,
    entity_type: str,
    entity_id: int,
    vote_value: int,
) -> dict:
    """
    Cast a jury vote on pending content.
    
    Args:
        db: Database session
        user_id: User casting the vote
        entity_type: 'author', 'book', or 'collection'
        entity_id: ID of the entity
        vote_value: 1 or 5 (calculated from user scopes)
        
    Returns:
        Dictionary with:
        - vote_score: Current vote score after this vote
        - auto_published: True if content was auto-published
        - threshold_met: True if threshold was met
        
    Raises:
        ValueError: If vote already exists or entity not PENDING
    """
    # Check if vote already exists
    existing_vote = await db.scalar(
        select(JuryVote).where(
            JuryVote.user_id == user_id,
            JuryVote.entity_type == entity_type,
            JuryVote.entity_id == entity_id,
        )
    )
    
    if existing_vote:
        raise ValueError("User has already voted on this content")
    
    # Get the entity
    entity_model = _get_entity_model(entity_type)
    entity = await db.get(entity_model, entity_id)
    
    if not entity:
        raise ValueError(f"{entity_type.capitalize()} not found")
    
    if entity.status != ContentStatus.PENDING:
        raise ValueError(f"Can only vote on PENDING content (current status: {entity.status})")
    
    # Create vote record
    vote = JuryVote(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        vote_value=vote_value,
    )
    db.add(vote)
    
    # Update entity vote_score (cap at 5 to respect CHECK constraint)
    entity.vote_score = min(entity.vote_score + vote_value, 5)
    current_score = entity.vote_score
    
    # Check if threshold met
    auto_published = False
    if current_score >= VOTE_THRESHOLD:
        # Auto-publish!
        entity.status = ContentStatus.APPROVED
        entity.is_public = True
        entity.version += 1
        
        # Record approval in edit history
        await record_approval(
            db=db,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,  # The voter who pushed it over threshold
            old_data=serialize_entity(entity),
            new_data=serialize_entity(entity),
            new_version=entity.version,
            old_version=entity.version - 1,
        )
        
        auto_published = True
        
        # Adjust trust score for submitter (+10 for author/collection, +20 for book)
        try:
            is_book = entity_type == "book"
            await adjust_trust_for_approval(
                user_id=entity.created_by_user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                is_book=is_book,
            )
        except Exception as e:
            # Log but don't fail the approval
            print(f"Warning: Failed to adjust trust score: {e}")
    
    await db.commit()
    
    return {
        "vote_score": current_score,
        "auto_published": auto_published,
        "threshold_met": current_score >= VOTE_THRESHOLD,
    }


async def retract_jury_vote(
    db: AsyncSession,
    user_id: UUID,
    entity_type: str,
    entity_id: int,
) -> dict:
    """
    Retract a jury vote (remove vote and decrement score).
    
    Args:
        db: Database session
        user_id: User retracting the vote
        entity_type: 'author', 'book', or 'collection'
        entity_id: ID of the entity
        
    Returns:
        Dictionary with:
        - vote_score: Current vote score after retraction
        - vote_value: Value of the retracted vote
        
    Raises:
        ValueError: If vote doesn't exist
    """
    # Find existing vote
    vote = await db.scalar(
        select(JuryVote).where(
            JuryVote.user_id == user_id,
            JuryVote.entity_type == entity_type,
            JuryVote.entity_id == entity_id,
        )
    )
    
    if not vote:
        raise ValueError("Vote not found")
    
    vote_value = vote.vote_value
    
    # Get entity and decrement score
    entity_model = _get_entity_model(entity_type)
    entity = await db.get(entity_model, entity_id)
    
    if entity:
        entity.vote_score = max(0, entity.vote_score - vote_value)  # Floor at 0
        current_score = entity.vote_score
    else:
        current_score = 0
    
    # Delete vote
    await db.delete(vote)
    await db.commit()
    
    return {
        "vote_score": current_score,
        "vote_value": vote_value,
    }


async def get_vote_status(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
) -> dict:
    """
    Get voting status for an entity.
    
    Args:
        db: Database session
        entity_type: 'author', 'book', or 'collection'
        entity_id: ID of the entity
        
    Returns:
        Dictionary with:
        - vote_score: Current total score
        - threshold: Threshold for auto-publish (5)
        - votes_needed: How many more votes needed (if any)
        - voter_count: Number of users who voted
        - voters: List of voter IDs
        - vote_breakdown: Dict of {user_id: vote_value}
    """
    # Get all votes for this entity
    votes_query = select(JuryVote).where(
        JuryVote.entity_type == entity_type,
        JuryVote.entity_id == entity_id,
    )
    
    result = await db.execute(votes_query)
    votes = result.scalars().all()
    
    vote_score = sum(v.vote_value for v in votes)
    voter_count = len(votes)
    votes_needed = max(0, VOTE_THRESHOLD - vote_score)
    
    return {
        "vote_score": vote_score,
        "threshold": VOTE_THRESHOLD,
        "votes_needed": votes_needed,
        "voter_count": voter_count,
        "voters": [str(v.user_id) for v in votes],
        "vote_breakdown": {str(v.user_id): v.vote_value for v in votes},
    }


async def clear_jury_votes(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
) -> int:
    """
    Clear all jury votes for an entity (used when curator overrides).
    
    Args:
        db: Database session
        entity_type: 'author', 'book', or 'collection'
        entity_id: ID of the entity
        
    Returns:
        Number of votes cleared
    """
    # Get count first
    count = await db.scalar(
        select(func.count()).select_from(JuryVote).where(
            JuryVote.entity_type == entity_type,
            JuryVote.entity_id == entity_id,
        )
    )
    
    # Delete all votes
    votes_query = select(JuryVote).where(
        JuryVote.entity_type == entity_type,
        JuryVote.entity_id == entity_id,
    )
    result = await db.execute(votes_query)
    votes = result.scalars().all()
    
    for vote in votes:
        await db.delete(vote)
    
    return count or 0


def _get_entity_model(entity_type: str):
    """Get SQLAlchemy model class for entity type."""
    if entity_type == "author":
        return Author
    elif entity_type == "book":
        return Book
    elif entity_type == "collection":
        return Collection
    else:
        raise ValueError(f"Invalid entity type: {entity_type}")
