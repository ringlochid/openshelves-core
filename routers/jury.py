"""
Jury voting router for democratic content approval.
Implements community voting system for PENDING content.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_async_db
from dependencies.auth import get_current_user, require_scope
from models import Author, ContentStatus
from schemas.author import AuthorRead, AuthorListResponse, AuthorDetail
from helpers.jury import (
    calculate_vote_weight,
    cast_jury_vote,
    retract_jury_vote,
    get_vote_status,
)


router = APIRouter(prefix="/jury", tags=["Jury Voting"])


# ========================================
# JURY QUEUE ENDPOINTS
# ========================================

@router.get("/authors", response_model=AuthorListResponse)
async def list_pending_authors(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("created_at", pattern="^(created_at|vote_score|name)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List pending authors in jury queue.
    Requires 'jury:view' scope (contributor: trust_score >= 10).
    
    Shows authors awaiting jury votes or curator approval.
    """
    # Base query - only show PENDING, non-deleted authors
    query = select(Author).where(
        and_(
            Author.status == ContentStatus.PENDING,
            Author.is_deleted == False,
        )
    )
    
    # Sorting
    order_col = getattr(Author, sort)
    if order == "desc":
        order_col = order_col.desc()
    query = query.order_by(order_col)
    
    # Get total count
    from sqlalchemy import func
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0
    
    # Pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)
    
    # Execute
    result = await db.execute(query)
    authors = result.scalars().all()
    
    return AuthorListResponse(
        items=[AuthorRead.model_validate(a) for a in authors],
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page,
    )


@router.get("/authors/{author_id}", response_model=AuthorDetail)
async def get_pending_author_detail(
    author_id: int,
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get detailed information for a pending author.
    Requires 'jury:view' scope.
    
    Shows full author details plus voting status.
    """
    query = select(Author).where(
        and_(
            Author.id == author_id,
            Author.status == ContentStatus.PENDING,
            Author.is_deleted == False,
        )
    )
    
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending author not found"
        )
    
    return AuthorDetail.model_validate(author)


# ========================================
# VOTING ENDPOINTS
# ========================================

@router.post("/authors/{author_id}/vote")
async def vote_on_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Cast a jury vote on a pending author.
    
    Vote weights:
    - Contributors with 'jury:vote': +1
    - Trusted users with 'jury:vote_weighted': +5
    
    Auto-publishes when vote_score >= 5 (awards +10 trust to submitter).
    """
    # Check if user has voting permissions
    user_scopes = current_user.get("scopes", [])
    
    if "jury:vote" not in user_scopes and "jury:vote_weighted" not in user_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: jury:vote or jury:vote_weighted"
        )
    
    # Calculate vote weight from scopes
    vote_value = calculate_vote_weight(user_scopes)
    
    if vote_value == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to vote"
        )
    
    # Verify author exists and is PENDING (lock to prevent concurrent modifications)
    query = select(Author).where(Author.id == author_id).with_for_update()
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    if author.status != ContentStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only vote on PENDING content (current status: {author.status})"
        )
    
    # Double-check not deleted (race condition guard)
    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Author has been deleted"
        )
    
    # Cast vote
    try:
        vote_result = await cast_jury_vote(
            db=db,
            user_id=current_user["user_id"],
            entity_type="author",
            entity_id=author_id,
            vote_value=vote_value,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    return {
        "message": "Vote cast successfully",
        "vote_value": vote_value,
        "vote_score": vote_result["vote_score"],
        "auto_published": vote_result["auto_published"],
        "threshold_met": vote_result["threshold_met"],
    }


@router.delete("/authors/{author_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def retract_vote_on_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Retract your jury vote on a pending author.
    Decrements the vote_score by your vote value.
    """
    # Check if user has voting permissions
    user_scopes = current_user.get("scopes", [])
    
    if "jury:vote" not in user_scopes and "jury:vote_weighted" not in user_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: jury:vote or jury:vote_weighted"
        )
    
    # Retract vote
    try:
        await retract_jury_vote(
            db=db,
            user_id=current_user["user_id"],
            entity_type="author",
            entity_id=author_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.get("/authors/{author_id}/votes")
async def get_author_vote_status(
    author_id: int,
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get voting status for a pending author.
    Shows current score, threshold, and who voted.
    """
    # Verify author exists
    query = select(Author).where(Author.id == author_id)
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    # Get vote status
    vote_status = await get_vote_status(
        db=db,
        entity_type="author",
        entity_id=author_id,
    )
    
    return vote_status
