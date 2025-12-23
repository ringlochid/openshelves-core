"""
Jury voting router for democratic content approval.
Implements community voting system for PENDING content.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_async_db
from cache import get_redis
from dependencies.auth import get_current_user, require_scope
from models import Author, Book, ContentStatus
from schemas.author import AuthorRead, AuthorListResponse, AuthorDetail
from schemas.book import BookDetail, BookListRead
from helpers.jury import (
    calculate_vote_weight,
    cast_jury_vote,
    retract_jury_vote,
    get_vote_status,
)
import cache
from cache import Redis
from uuid import UUID
from settings import settings


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
    r: Redis = Depends(cache.get_redis),
):
    """
    List pending authors in jury queue.
    Requires 'jury:view' scope (contributor: trust_score >= 10).

    Shows authors awaiting jury votes or curator approval.
    Cached with version-based invalidation (bumped when voting/approval happens).
    """
    rl_key = cache.make_rate_limit_key(
        "jury:authors:list", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Try cache first
    params = {"page": page, "per_page": per_page, "sort": sort, "order": order}
    cached = await cache.get_list("jury:authors", params, r=r)
    if cached is not None:
        return cached
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
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    authors = result.scalars().all()

    response_data = AuthorListResponse(
        items=[AuthorRead.model_validate(a) for a in authors],
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page,
    )

    # Cache the result
    await cache.cache_list(
        "jury:authors", params, response_data.model_dump(mode="json"), r=r
    )

    return response_data


@router.get("/authors/{author_id}", response_model=AuthorDetail)
async def get_pending_author_detail(
    author_id: int,
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Get detailed information for a pending author.
    Requires 'jury:view' scope.

    Shows full author details plus voting status.
    Uses author cache (invalidated on any author change).
    """
    rl_key = cache.make_rate_limit_key(
        "jury:authors:get", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Try cache first (uses same author cache as public endpoint)
    cached = await cache.get_author(author_id, r)
    if cached and cached.get("status") == "PENDING":
        return AuthorDetail.model_validate(cached)
    query = (
        select(Author)
        .where(
            and_(
                Author.id == author_id,
                Author.status == ContentStatus.PENDING,
                Author.is_deleted == False,
            )
        )
        .options(selectinload(Author.books))
    )

    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pending author not found"
        )

    # Cache the result
    author_dict = AuthorDetail.model_validate(author).model_dump(mode="json")
    await cache.cache_author(author_id, author_dict, r)

    return AuthorDetail.model_validate(author)


# ========================================
# VOTING ENDPOINTS
# ========================================


@router.post("/authors/{author_id}/vote")
async def vote_on_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r=Depends(get_redis),
):
    """
    Cast a jury vote on a pending author.

    Vote weights:
    - Contributors with 'jury:vote': +1
    - Trusted users with 'jury:vote_weighted': +5

    Auto-publishes when vote_score >= 5 (awards +10 trust to submitter).
    """
    rl_key = cache.make_rate_limit_key(
        "jury:authors:vote", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Check if user has voting permissions
    user_scopes = current_user.get("scopes", [])

    if "jury:vote" not in user_scopes and "jury:vote_weighted" not in user_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: jury:vote or jury:vote_weighted",
        )

    # Calculate vote weight from scopes
    vote_value = calculate_vote_weight(user_scopes)

    if vote_value == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to vote",
        )

    # Verify author exists and is PENDING (lock to prevent concurrent modifications)
    # Eagerly load books relationship for cache invalidation
    query = (
        select(Author)
        .where(Author.id == author_id)
        .options(selectinload(Author.books))
        .with_for_update()
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    if author.status != ContentStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only vote on PENDING content (current status: {author.status})",
        )

    # Double-check not deleted (race condition guard)
    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Author has been deleted"
        )

    # Cast vote
    try:
        vote_result = await cast_jury_vote(
            db=db,
            user_id=current_user["user_id"],
            entity_type="author",
            entity_id=author_id,
            vote_value=vote_value,
            entity=author,  # Pass eagerly-loaded entity
            redis_client=r,  # Pass redis for cache invalidation
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

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
    r: Redis = Depends(cache.get_redis),
):
    """
    Retract your jury vote on a pending author.
    Decrements the vote_score by your vote value.
    """
    rl_key = cache.make_rate_limit_key(
        "jury:authors:unvote", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_WRITE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_WRITE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_WRITE_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Check if user has voting permissions
    user_scopes = current_user.get("scopes", [])

    if "jury:vote" not in user_scopes and "jury:vote_weighted" not in user_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: jury:vote or jury:vote_weighted",
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/authors/{author_id}/votes")
async def get_author_vote_status(
    author_id: int,
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Get voting status for a pending author.
    Shows current score, threshold, and who voted.
    """
    rl_key = cache.make_rate_limit_key(
        "jury:authors:votes", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Verify author exists
    query = select(Author).where(Author.id == author_id)
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Get vote status
    vote_status = await get_vote_status(
        db=db,
        entity_type="author",
        entity_id=author_id,
    )

    return vote_status


# ========================================
# BOOK JURY ENDPOINTS
# ========================================


@router.get("/books", response_model=dict)
async def list_pending_books(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("created_at", pattern="^(created_at|vote_score|title)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    List pending books in jury queue.
    Requires 'jury:view' scope (contributor: trust_score >= 10).

    Shows books awaiting jury votes or curator approval.
    """
    rl_key = cache.make_rate_limit_key(
        "jury:books:list", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Try cache first
    params = {"page": page, "per_page": per_page, "sort": sort, "order": order}
    cached = await cache.get_list("jury:books", params, r=r)
    if cached is not None:
        return cached

    # Base query - only show PENDING, non-deleted books
    query = select(Book).where(
        and_(
            Book.status == ContentStatus.PENDING,
            Book.is_deleted == False,
        )
    )

    # Sorting
    order_col = getattr(Book, sort)
    if order == "desc":
        query = query.order_by(order_col.desc())
    else:
        query = query.order_by(order_col.asc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    books = result.scalars().all()

    response = {
        "items": [BookListRead.model_validate(book) for book in books],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }

    # Cache the response
    await cache.cache_list("jury:books", params, response, r=r)

    return response


@router.get("/books/{book_id}", response_model=BookDetail)
async def get_pending_book_detail(
    book_id: int,
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """Get detailed information about a pending book in the jury queue."""
    rl_key = cache.make_rate_limit_key(
        "jury:books:get", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    query = (
        select(Book)
        .where(
            and_(
                Book.id == book_id,
                Book.status == ContentStatus.PENDING,
                Book.is_deleted == False,
            )
        )
        .options(selectinload(Book.authors), selectinload(Book.reviews))
    )
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found in jury queue"
        )

    return BookDetail.model_validate(book)


@router.post("/books/{book_id}/vote")
async def vote_on_book(
    book_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r=Depends(get_redis),
):
    """
    Cast a jury vote on a pending book.
    Vote weight based on user scopes (contributor=1, trusted=5).
    """
    rl_key = cache.make_rate_limit_key(
        "jury:books:vote", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Verify book is PENDING (eagerly load authors for cache invalidation)
    query = (
        select(Book)
        .where(Book.id == book_id)
        .options(selectinload(Book.authors))
        .with_for_update()
    )
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book or book.status != ContentStatus.PENDING or book.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found in jury queue or is deleted",
        )

    # Cast vote (handles weight calculation, duplicate prevention, auto-approval)
    vote_value = calculate_vote_weight(current_user.get("scopes", []))

    if vote_value == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to vote",
        )

    result = await cast_jury_vote(
        db=db,
        user_id=current_user["user_id"],
        entity_type="book",
        entity_id=book_id,
        vote_value=vote_value,
        entity=book,  # Pass eagerly-loaded entity
        redis_client=r,  # Pass redis for cache invalidation
    )

    await db.commit()

    return {
        "message": "Vote cast successfully",
        "vote_weight": result["vote_weight"],
        "new_vote_score": result["new_vote_score"],
        "auto_approved": result.get("auto_approved", False),
    }


@router.delete("/books/{book_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def retract_vote_on_book(
    book_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """Retract your jury vote on a pending book."""
    rl_key = cache.make_rate_limit_key(
        "jury:books:unvote", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_WRITE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_WRITE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_WRITE_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )
    try:
        await retract_jury_vote(
            db=db,
            entity_type="book",
            entity_id=book_id,
            user_id=current_user["user_id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    await db.commit()


@router.get("/books/{book_id}/votes")
async def get_book_vote_status(
    book_id: int,
    current_user: dict = Depends(require_scope("jury:view")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """Get voting status and statistics for a pending book."""
    rl_key = cache.make_rate_limit_key(
        "jury:books:votes", current_user.get("user_id") or "unknown"
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    status = await get_vote_status(
        db=db,
        entity_type="book",
        entity_id=book_id,
    )

    return {
        "has_voted": status["has_voted"],
        "vote_weight": status["vote_weight"],
        "total_votes": status["total_votes"],
        "threshold": status["threshold"],
    }
