"""
Author router with wiki-style workflow and RBAC.
Implements Phase 2 author management endpoints.
"""

import logging
from helpers.cursor import decode_cursor, encode_cursor
from datetime import datetime, timezone, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_async_db
from dependencies.auth import (
    get_current_user,
    get_current_user_optional,
    require_scope,
    require_role,
    require_min_trust,
)
from models import Author, Book, AuthorFollow, ContentStatus, EditHistory, EditAction
from schemas.author import (
    AuthorCreate,
    AuthorReplace,
    AuthorUpdate,
    AuthorDetail,
    AuthorRead,
    AuthorListResponse,
    AuthorListCursorResponse,
    AuthorRollbackRequest,
)
from schemas.shared import BookRead
from helpers.edit_history import (
    check_version_conflict,
    record_create,
    record_update,
    record_delete,
    record_approval,
    record_rejection,
    serialize_entity,
)
from helpers.jury import clear_jury_votes
from services.auth_client import adjust_trust_for_approval, adjust_trust_for_rejection

# NOTE: adjust_trust_for_social_bonus removed - social trust rewards deprecated in Phase 2.4
import cache
from cache import Redis
from services.auth_client import validate_user_exists
from helpers.request import get_request_ip
from settings import settings


router = APIRouter(prefix="/authors", tags=["Authors"])

logger = logging.getLogger(__name__)

# ========================================
# PUBLIC ENDPOINTS (No Auth Required)
# ========================================


@router.get("", response_model=AuthorListCursorResponse)
async def list_authors(
    search: str | None = Query(
        None, description="Search by name or email (uses trigram similarity)"
    ),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    List approved, public authors with similarity search and cursor pagination.

    - If search is provided, uses trigram similarity (70% name, 30% email weight)
    - Results sorted by similarity score DESC, then id ASC
    - Uses cursor-based pagination for consistent results
    - No authentication required
    """
    rl_key = cache.make_rate_limit_key("authors:list", ip or "unknown")
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Base conditions
    base_conditions = and_(
        Author.status == ContentStatus.APPROVED,
        Author.is_public == True,
        Author.is_deleted == False,
    )

    if search:
        # Trigram similarity search with weighted scoring
        # Name: 70%, Email: 30%
        name_sim = func.similarity(Author.name, search)
        email_sim = func.coalesce(func.similarity(Author.email, search), 0.0)
        score = (name_sim * 0.7 + email_sim * 0.3).label("similarity_score")

        # Use % operator for trigram matching (requires threshold met)
        search_filter = or_(Author.name.op("%")(search), Author.email.op("%")(search))

        query = (
            select(Author, score)
            .where(and_(base_conditions, search_filter))
            .order_by(score.desc(), Author.id.asc())
        )

        # Cursor pagination for search results
        if cursor:
            cursor_data = decode_cursor(cursor)
            last_score = cursor_data.get("score")
            last_id = cursor_data.get("id")

            if last_score is not None and last_id is not None:
                # Keyset pagination: (score < last_score) OR (score = last_score AND id > last_id)
                query = query.where(
                    or_(
                        score < last_score,
                        and_(score == last_score, Author.id > last_id),
                    )
                )

        query = query.limit(limit + 1)  # +1 to check if more pages exist
        result = await db.execute(query)
        rows = result.all()

        # Extract authors and scores
        authors = [row[0] for row in rows]
        scores = [float(row[1]) for row in rows]

    else:
        # No search - return all by name
        query = (
            select(Author)
            .where(base_conditions)
            .order_by(Author.name.asc(), Author.id.asc())
        )

        # Cursor pagination for listing
        if cursor:
            cursor_data = decode_cursor(cursor)
            last_name = cursor_data.get("name")
            last_id = cursor_data.get("id")

            if last_name is not None and last_id is not None:
                query = query.where(
                    or_(
                        Author.name > last_name,
                        and_(Author.name == last_name, Author.id > last_id),
                    )
                )

        query = query.limit(limit + 1)
        result = await db.execute(query)
        authors = result.scalars().all()
        scores = None

    # Check if more pages exist
    has_more = len(authors) > limit
    if has_more:
        authors = authors[:limit]
        if scores:
            scores = scores[:limit]

    # Generate next cursor
    next_cursor = None
    if has_more and authors:
        last_author = authors[-1]
        if search and scores:
            next_cursor = encode_cursor({"score": scores[-1], "id": last_author.id})
        else:
            next_cursor = encode_cursor(
                {"name": last_author.name, "id": last_author.id}
            )

    return AuthorListCursorResponse(
        items=[AuthorRead.model_validate(a) for a in authors],
        next_cursor=next_cursor,
    )


# special endpoint for owner to check author - must be before /{author_id}
@router.get("/me", response_model=AuthorListResponse)
async def get_my_authors(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Get all authors created by the current user.
    Shows all statuses (PENDING, APPROVED, REJECTED) for owner management.
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "authors:me", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    user_id = current_user["user_id"]

    # Build query for user's authors (any status, not deleted)
    query = (
        select(Author)
        .where(
            and_(
                Author.created_by_user_id == user_id,
                Author.is_deleted == False,
            )
        )
        .order_by(Author.created_at.desc())
    )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination
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


@router.get("/{author_id}", response_model=AuthorDetail)
async def get_author(
    author_id: int,
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
    current_user: dict | None = Depends(get_current_user_optional),
):
    """
    Get detailed author information.
    Approved, public authors visible to everyone.
    Owners can see their own pending/non-public authors.
    """
    rl_key = cache.make_rate_limit_key("authors:get", ip or "unknown")
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Try cache first (only for public/approved)
    cached = await cache.get_author(author_id, r)
    if (
        cached
        and cached.get("status") == "APPROVED"
        and cached.get("is_public") is True
    ):
        return AuthorDetail.model_validate(cached)

    # Query without status filter first to check ownership
    query = (
        select(Author)
        .where(
            and_(
                Author.id == author_id,
                Author.is_deleted == False,
            )
        )
        .options(selectinload(Author.books))
    )

    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Access control
    is_owner = current_user and author.created_by_user_id == current_user.get("user_id")
    is_public_approved = author.status == ContentStatus.APPROVED and author.is_public

    if not is_public_approved and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Cache only public/approved authors
    if is_public_approved:
        author_dict = AuthorDetail.model_validate(author).model_dump(mode="json")
        await cache.cache_author(author_id, author_dict, r)

    return AuthorDetail.model_validate(author)


@router.get("/{author_id}/books", response_model=list[BookRead])
async def get_author_books(
    author_id: int,
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Get all approved books by an author.
    Only shows books from approved, public authors.
    """
    rl_key = cache.make_rate_limit_key("authors:books:get", ip or "unknown")
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_READ_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_READ_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_READ_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Verify author exists and is public
    author_query = select(Author).where(
        and_(
            Author.id == author_id,
            Author.status == ContentStatus.APPROVED,
            Author.is_public == True,
            Author.is_deleted == False,
        )
    )
    author_result = await db.execute(author_query)
    author = author_result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Get approved books by this author
    books_query = (
        select(Book)
        .options(selectinload(Book.authors))
        .where(
            and_(
                Book.authors.any(Author.id == author_id),
                Book.status == ContentStatus.APPROVED,
                Book.is_public == True,
                Book.is_deleted == False,
            )
        )
        .order_by(Book.title)
    )

    result = await db.execute(books_query)
    books = result.scalars().all()

    return [BookRead.model_validate(book) for book in books]


# ========================================
# AUTHENTICATED ENDPOINTS
# ========================================


@router.post("", response_model=AuthorDetail, status_code=status.HTTP_201_CREATED)
async def create_author(
    data: AuthorCreate,
    current_user: dict = Depends(require_scope("authors:draft")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Submit a new author for approval.
    Requires 'authors:draft' scope (available to all users).

    Two possible paths:
    1. Trusted users with 'authors:publish_direct' → status=APPROVED (bypasses jury queue)
    2. Regular users → status=PENDING (requires jury voting or curator approval)
    """
    rl_key = cache.make_rate_limit_key(
        "authors:create", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Validate book_ids if provided
    books = []
    if data.book_ids:
        book_query = select(Book).where(
            and_(
                Book.id.in_(data.book_ids),
                Book.status == ContentStatus.APPROVED,
                Book.is_public == True,
                Book.is_deleted == False,
            )
        )
        result = await db.execute(book_query)
        books = result.scalars().all()

        if len(books) != len(data.book_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more book IDs are invalid or not approved",
            )

    # Check if user can publish directly (trusted bypass)
    user_scopes = current_user.get("scopes", [])
    can_publish_direct = "authors:publish_direct" in user_scopes

    # Validate linked_user_id against Auth Service
    if data.linked_user_id:
        await validate_user_exists(data.linked_user_id)

    # Create author with status based on user privileges
    author = Author(
        name=data.name,
        email=data.email,
        bio=data.bio,
        # Note: avatar_key set via upload pipeline, not create endpoint
        created_by_user_id=current_user["user_id"],
        linked_user_id=data.linked_user_id,
        status=ContentStatus.APPROVED if can_publish_direct else ContentStatus.PENDING,
        is_public=can_publish_direct,  # Public immediately if direct publish
        vote_score=0,  # Always start at 0 (even if direct publish doesn't need it)
        version=1,
    )

    if books:
        author.books = list(books)

    db.add(author)
    await db.flush()  # Get author.id before recording history

    # Record creation in edit history
    await db.refresh(author, ["books"])  # Load relationship for serialization
    await record_create(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        data=serialize_entity(author),
    )

    # NOTE: Direct publish does NOT award trust - prevents spam exploit.
    # Trusted users could otherwise farm trust by repeatedly publishing content.
    # Trust is only awarded when content goes through jury/curator approval.

    await db.commit()

    # Invalidate author lists cache (new author added)
    # Pass book_ids explicitly to avoid DB query in cache layer
    book_ids = [book.id for book in books] if books else []
    await cache.invalidate_author(author.id, r, book_ids=book_ids)

    # Refresh with books relationship loaded to avoid lazy load issues
    query = (
        select(Author).where(Author.id == author.id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one()

    return AuthorDetail.model_validate(author)


@router.put("/{author_id}", response_model=AuthorDetail)
async def replace_author(
    author_id: int,
    data: AuthorReplace,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Replace an author (full update with optimistic locking).
    Requires scope "authors:update_own" or "authors:update_public_meta".
    """
    rl_key = cache.make_rate_limit_key(
        "authors:replace", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch author with books
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Check permissions
    is_owner = author.created_by_user_id == current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    has_update_own = "authors:update_own" in user_scopes
    has_update_public_meta = "authors:update_public_meta" in user_scopes

    if is_owner and has_update_own:
        # Owner can update their own author
        pass
    elif has_update_public_meta and author.status == ContentStatus.APPROVED:
        # Wiki editor can update any APPROVED author
        pass
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions. Owner needs 'authors:update_own' or wiki-editor needs 'authors:update_public_meta' (APPROVED only)",
        )

    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(author)
    old_version = author.version

    # Check version for optimistic locking
    check_version_conflict(author.version, data.version, "author", author_id)
    # Validate book_ids if provided
    books = []
    affected_books_ids = []
    if data.book_ids:
        book_query = select(Book).where(
            and_(
                Book.id.in_(data.book_ids),
                Book.status == ContentStatus.APPROVED,
                Book.is_public == True,
                Book.is_deleted == False,
            )
        )
        result = await db.execute(book_query)
        books = result.scalars().all()

        if len(books) != len(data.book_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more book IDs are invalid or not approved",
            )

        affected_books_ids = [book.id for book in books]

    # Update author
    author.name = data.name
    author.email = data.email
    author.bio = data.bio
    # Note: avatar_key not settable via PUT - use upload endpoints
    author.books = list(books)

    if data.linked_user_id:
        await validate_user_exists(data.linked_user_id)

    author.linked_user_id = data.linked_user_id

    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)
    author.updated_at = datetime.now(timezone.utc)

    db.add(author)
    await db.flush()

    # Record update in edit history
    await db.refresh(author, ["books"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(author),
        old_version=old_version,
        new_version=author.version,
    )

    affected_books_ids.extend([book.id for book in author.books])

    await db.commit()

    # Invalidate author lists cache (new author added)
    # Pass book_ids explicitly to avoid DB query in cache layer
    await cache.invalidate_author(author.id, r, book_ids=affected_books_ids)

    # Refresh with books relationship loaded to avoid lazy load issues
    await db.refresh(author)

    return AuthorDetail.model_validate(author)


@router.patch("/{author_id}", response_model=AuthorDetail)
async def update_author(
    author_id: int,
    data: AuthorUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Update an author.

    Permission Matrix:
    - Owner with 'authors:update_own': Can update OWN author (any status)
    - Non-owner with 'authors:edit_public_meta': Can update ANY APPROVED author (wiki mode)
    - Both: Allowed (owner overrides wiki-editor restrictions)
    """
    rl_key = cache.make_rate_limit_key(
        "authors:update", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_WRITE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_WRITE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_WRITE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch author with books
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Check version for optimistic locking
    check_version_conflict(author.version, data.version, "author", author_id)

    # Check permissions
    is_owner = author.created_by_user_id == current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    has_update_own = "authors:update_own" in user_scopes
    has_edit_public_meta = "authors:edit_public_meta" in user_scopes

    # Permission logic:
    # 1. Owner with authors:update_own → can update own author (any status)
    # 2. Non-owner with authors:edit_public_meta → can update ANY APPROVED author (wiki mode)
    if is_owner and has_update_own:
        # Owner can update their own author
        pass
    elif has_edit_public_meta and author.status == ContentStatus.APPROVED:
        # Wiki editor can update any APPROVED author
        pass
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions. Owner needs 'authors:update_own' or wiki-editor needs 'authors:edit_public_meta' (APPROVED only)",
        )

    # Store old data for history (serialize BEFORE any modifications)
    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(author)

    # Capture previous book_ids BEFORE modifying (needed for cache invalidation)
    previous_book_ids = {book.id for book in author.books} if author.books else set()

    # Update fields
    if data.name is not None:
        author.name = data.name
    if data.email is not None:
        author.email = data.email
    if data.bio is not None:
        author.bio = data.bio
    # Note: avatar_key not settable via PATCH - use upload endpoints
    if data.linked_user_id is not None:
        await validate_user_exists(data.linked_user_id)
        author.linked_user_id = data.linked_user_id

    # Update book associations if provided
    if data.book_ids is not None:
        book_query = select(Book).where(
            and_(
                Book.id.in_(data.book_ids),
                Book.status == ContentStatus.APPROVED,
                Book.is_public == True,
                Book.is_deleted == False,
            )
        )
        result = await db.execute(book_query)
        found_books = result.scalars().all()

        # Check if some books are missing (deleted or unapproved)
        found_book_ids = {book.id for book in found_books}
        missing_book_ids = set(data.book_ids) - found_book_ids

        if missing_book_ids:
            # Log warning but allow partial update (books may have been deleted/rejected after initial association)
            logger.warning(
                "Cannot associate with books %s - they are deleted, rejected, or don't exist",
                missing_book_ids,
            )

        author.books = list(found_books)

    # Increment version and update metadata
    old_version = author.version
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)

    # Record update in edit history
    await db.refresh(author, ["books"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=old_data,  # Already serialized above
        new_data=serialize_entity(author),
        new_version=author.version,
        old_version=old_version,
    )

    await db.commit()

    # Invalidate caches - union OLD and NEW book_ids to catch removed books
    new_book_ids = {book.id for book in author.books} if author.books else set()
    affected_book_ids = list(previous_book_ids | new_book_ids)
    await cache.invalidate_author(author_id, r, book_ids=affected_book_ids)

    # Refresh with books relationship loaded to avoid lazy load issues
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one()

    return AuthorDetail.model_validate(author)


@router.post("/{author_id}/rollback", response_model=AuthorDetail)
async def rollback_author_version(
    author_id: int,
    data: AuthorRollbackRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Rollback author to a previous version from edit history.

    Permission required:
    - Owner with 'authors:update_own' scope, OR
    - User with 'authors:edit_public_meta' scope (wiki editor)

    Creates a new version with the old data (does not revert version number).
    """
    rl_key = cache.make_rate_limit_key(
        "authors:rollback", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch author
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Check version conflict
    if author.version != data.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version conflict. Current version is {author.version}, you have {data.version}",
        )

    # Permission check
    is_owner = author.created_by_user_id == current_user["user_id"]
    has_update_own = "authors:update_own" in current_user.get("scopes", [])
    has_edit_public_meta = "authors:edit_public_meta" in current_user.get("scopes", [])

    if is_owner and has_update_own:
        pass
    elif has_edit_public_meta and author.status == ContentStatus.APPROVED:
        pass
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to rollback this author",
        )

    # Fetch target version from edit history
    history_query = (
        select(EditHistory)
        .where(
            and_(
                EditHistory.entity_type == "author",
                EditHistory.entity_id == author_id,
                EditHistory.version == data.target_version,
            )
        )
        .order_by(EditHistory.created_at.desc())
        .limit(1)
    )

    result = await db.execute(history_query)
    target_record = result.scalar_one_or_none()

    if not target_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {data.target_version} not found in edit history",
        )

    # Capture previous book_ids BEFORE rollback (needed for cache invalidation)
    previous_book_ids = {book.id for book in author.books} if author.books else set()

    # Capture old state BEFORE rollback for audit
    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data_for_audit = serialize_entity(author)
    old_version = author.version

    # Apply old data to current entity
    old_data = target_record.new_data
    restored_changes = target_record.changes
    if not restored_changes or restored_changes.get("total_changes") == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Version {data.target_version} has no changes to restore",
        )
    if old_data:
        # Update fields from old version
        if "name" in old_data:
            author.name = old_data["name"]
        if "email" in old_data:
            author.email = old_data["email"]
        if "bio" in old_data:
            author.bio = old_data["bio"]
        if "avatar_key" in old_data:
            author.avatar_key = old_data["avatar_key"]
        if "linked_user_id" in old_data:
            author.linked_user_id = old_data["linked_user_id"]

        # Restore book associations if present
        if "book_ids" in old_data:
            book_ids = old_data["book_ids"]
            # Fetch books including deleted ones (historical restoration should preserve associations)
            books_result = await db.execute(select(Book).where(Book.id.in_(book_ids)))
            found_books = books_result.scalars().all()

            # Check if some books are missing (hard deleted from database)
            found_book_ids = {book.id for book in found_books}
            missing_book_ids = set(book_ids) - found_book_ids

            if missing_book_ids:
                # Log warning but continue with partial restoration
                print(
                    f"Warning: Cannot restore associations with books {missing_book_ids} - they no longer exist in database"
                )

            author.books = list(found_books)  # type: ignore

        # Warn if linked_user_id user no longer valid in auth service
        # Use try/except to allow rollback to proceed even if user check fails
        if "linked_user_id" in old_data and old_data["linked_user_id"]:
            try:
                await validate_user_exists(UUID(old_data["linked_user_id"]))
            except Exception as e:
                logger.warning(
                    "Restoring linked_user_id %s but user validation failed: %s. Proceeding with rollback.",
                    old_data["linked_user_id"],
                    e,
                )

    # Increment version (rollback creates new version)
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)

    # Record rollback in history with correct pre/post snapshots
    await db.refresh(author, ["books"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=old_data_for_audit,
        new_data=serialize_entity(author),
        new_version=author.version,
        old_version=old_version,
    )

    await db.commit()

    # Invalidate caches - union OLD and NEW book_ids to catch removed books
    new_book_ids = {book.id for book in author.books} if author.books else set()
    affected_book_ids = list(previous_book_ids | new_book_ids)
    await cache.invalidate_author(author_id, r, book_ids=affected_book_ids)

    await db.refresh(author)

    # Reload relationships
    await db.execute(
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )

    return AuthorDetail.model_validate(author)


@router.delete("/{author_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_own_author(
    author_id: int,
    current_user: dict = Depends(require_scope("authors:delete_own")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Delete own author (owner only).
    Soft-deletes the author by setting is_deleted=True.
    Requires 'authors:delete_own' scope + ownership.
    """
    rl_key = cache.make_rate_limit_key(
        "authors:delete", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch author with books
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    # Check ownership
    if author.created_by_user_id != current_user["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own authors",
        )

    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Author is already deleted"
        )

    # Soft delete
    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(author)
    author.is_deleted = True
    author.deleted_at = datetime.now(timezone.utc)
    author.is_public = False
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)

    # Record deletion in edit history
    await record_delete(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        data=old_data,
        version=author.version - 1,
    )

    await db.commit()

    # Invalidate caches - pass book_ids to avoid DB query
    book_ids = [book.id for book in author.books] if author.books else []
    await cache.invalidate_author(author_id, r, book_ids=book_ids)


@router.post("/{author_id}/recover", response_model=AuthorDetail)
async def recover_deleted_author(
    author_id: int,
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Recover a soft-deleted author (curator only).
    Only works within 24 hours of deletion.
    After 24h, content is hard-deleted by background worker.

    Requires 'jury:override' scope (curator role: trust_score >= 80, reputation >= 90%).
    """
    rl_key = cache.make_rate_limit_key(
        "authors:recover", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch deleted author
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    if not author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Author is not deleted"
        )

    # Check 24-hour window
    if author.deleted_at:
        time_since_deletion = datetime.now(timezone.utc) - author.deleted_at
        if time_since_deletion > timedelta(hours=24):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Recovery window expired. Content deleted more than 24 hours ago.",
            )

    # Restore author
    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(author)
    author.is_deleted = False
    author.deleted_at = None

    # Set is_public based on status
    if author.status == ContentStatus.APPROVED:
        author.is_public = True
    else:
        author.is_public = False

    old_version = author.version
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)

    # Record recovery in edit history
    await db.refresh(author, ["books"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(author),
        new_version=author.version,
        old_version=old_version,
    )

    await db.commit()

    # Invalidate caches - pass book_ids to avoid DB query
    book_ids = [book.id for book in author.books] if author.books else []
    await cache.invalidate_author(author_id, r, book_ids=book_ids)

    # Reload with books relationship to avoid lazy load issues
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one()

    return AuthorDetail.model_validate(author)


# TODO integrate with reputation system in auth&user service,
# reputation = approved_count_of_all_entities + 3/ total_count_of_all_entities + 3,
# check auth service if there is such column and endpoint, if not, implement that first


@router.delete("/{author_id}/admin", status_code=status.HTTP_204_NO_CONTENT)
async def takedown_author(
    author_id: int,
    current_user: dict = Depends(require_scope("content:takedown")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Hard removal of author (curator/admin only).
    Used for DMCA, illegal content, spam removal.
    Requires 'content:takedown' scope (curator role: trust_score >= 80, reputation >= 90%).
    """
    rl_key = cache.make_rate_limit_key(
        "authors:delete", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch author
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Author is already deleted"
        )

    # Soft delete (hard removal)
    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(author)
    author.is_deleted = True
    author.deleted_at = datetime.now(timezone.utc)
    author.is_public = False
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)

    # Record takedown in edit history
    await record_delete(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        data=old_data,
        version=author.version - 1,
    )

    await db.commit()

    # Invalidate caches - pass book_ids to avoid DB query
    book_ids = [book.id for book in author.books] if author.books else []
    await cache.invalidate_author(author_id, r, book_ids=book_ids)


@router.post("/{author_id}/follow", status_code=status.HTTP_201_CREATED)
async def follow_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Follow an author (no trust reward).
    Requires authentication.
    """
    rl_key = cache.make_rate_limit_key(
        "authors:follow", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_WRITE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_WRITE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_WRITE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Verify author exists and is approved (with lock to prevent race conditions)
    query = (
        select(Author)
        .where(
            and_(
                Author.id == author_id,
                Author.status == ContentStatus.APPROVED,
                Author.is_public == True,
                Author.is_deleted == False,
            )
        )
        .with_for_update()
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found or not approved",
        )

    # Double-check not deleted (race condition guard)
    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Author has been deleted"
        )

    # Check if already following
    existing = await db.execute(
        select(AuthorFollow).where(
            and_(
                AuthorFollow.user_id == current_user["user_id"],
                AuthorFollow.author_id == author_id,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already following this author",
        )

    # Create follow relationship
    follow = AuthorFollow(
        user_id=current_user["user_id"],
        author_id=author_id,
    )
    db.add(follow)

    # Increment follower count
    author.follower_count += 1

    await db.commit()

    # Invalidate author cache (follower_count changed)
    await cache.invalidate_author_follows(author_id, r)

    # NOTE: Social trust rewards removed to prevent follow/unfollow exploit loop.
    # Trust is only awarded for content approval and helpful reviews.


@router.delete("/{author_id}/follow", status_code=status.HTTP_204_NO_CONTENT)
async def unfollow_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Unfollow an author.
    Decrements follower count.
    Requires authentication.
    """
    rl_key = cache.make_rate_limit_key(
        "authors:unfollow", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_WRITE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_WRITE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_WRITE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Find follow relationship
    query = select(AuthorFollow).where(
        and_(
            AuthorFollow.user_id == current_user["user_id"],
            AuthorFollow.author_id == author_id,
        )
    )
    result = await db.execute(query)
    follow = result.scalar_one_or_none()

    if not follow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not following this author",
        )

    # Get author to decrement follower count
    author_query = select(Author).where(Author.id == author_id)
    author_result = await db.execute(author_query)
    author = author_result.scalar_one_or_none()

    if author:
        author.follower_count = max(0, author.follower_count - 1)

    # Delete follow relationship
    await db.delete(follow)
    await db.commit()

    # Invalidate author cache (follower_count changed)
    if author:
        await cache.invalidate_author_follows(author_id, r)


# ========================================
# ADMIN/CURATOR ENDPOINTS
# ========================================


@router.post("/{author_id}/approve", response_model=AuthorDetail)
async def approve_author(
    author_id: int,
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Curator instant approval (bypasses jury voting).
    Requires 'jury:override' scope (curator role: trust_score >= 80, reputation >= 90%).
    Clears any existing jury votes and approves immediately.
    Awards +10 trust points to submitter.
    """
    rl_key = cache.make_rate_limit_key(
        "authors:approve", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch author (lock to prevent concurrent approvals/rejections)
    query = select(Author).where(Author.id == author_id).with_for_update()
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    if author.status == ContentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Author is already approved"
        )

    # Check not deleted (race condition guard)
    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Cannot approve deleted author"
        )

    # Clear any existing jury votes (curator override bypasses voting)
    votes_cleared = await clear_jury_votes(db, "author", author_id)

    # Update status
    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(author)
    author.status = ContentStatus.APPROVED
    author.is_public = True
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)

    # Record approval in edit history
    await db.refresh(author, ["books"])  # Refresh after changes for new_data
    await record_approval(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(author),
        new_version=author.version,
        old_version=author.version - 1,
    )

    await db.commit()

    # Adjust trust score (+10 for author approval)
    try:
        await adjust_trust_for_approval(
            user_id=author.created_by_user_id,
            entity_type="author",
            entity_id=author.id,
            is_book=False,
        )
    except Exception as e:
        # Log but don't fail the approval
        logger.warning("Failed to adjust trust score: %s", e)

    # Refresh with books relationship loaded to avoid lazy load issues
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one()

    # Invalidate caches (status changed, now public) - pass book_ids to avoid DB query
    book_ids = [book.id for book in author.books] if author.books else []
    await cache.invalidate_author(author_id, r, book_ids=book_ids)

    return AuthorDetail.model_validate(author)


@router.post("/{author_id}/reject", response_model=AuthorDetail)
async def reject_author(
    author_id: int,
    reason: str = Query(..., description="Rejection reason"),
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Curator instant rejection (bypasses jury voting).
    Requires 'jury:override' scope (curator role: trust_score >= 80, reputation >= 90%).
    Clears any existing jury votes and rejects immediately.
    Deducts -5 trust points from submitter.
    """
    rl_key = cache.make_rate_limit_key(
        "authors:reject", str(current_user.get("user_id") or "unknown")
    )
    allowed, _ = await cache.token_bucket_allow(
        key=rl_key,
        capacity=settings.RATE_LIMIT_SENSITIVE_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_SENSITIVE_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_SENSITIVE_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Fetch author (lock to prevent concurrent approvals/rejections)
    query = select(Author).where(Author.id == author_id).with_for_update()
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Author not found"
        )

    if author.status == ContentStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Author is already rejected"
        )

    # Check not deleted (race condition guard)
    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Cannot reject deleted author"
        )

    # Clear any existing jury votes (curator override bypasses voting)
    votes_cleared = await clear_jury_votes(db, "author", author_id)

    # Update status
    await db.refresh(author, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(author)
    author.status = ContentStatus.REJECTED
    author.is_public = False
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)

    # Record rejection in edit history
    await db.refresh(author, ["books"])  # Refresh after changes for new_data
    await record_rejection(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(author),
        new_version=author.version,
        old_version=author.version - 1,
    )

    await db.commit()

    # Adjust trust score (-5 for author rejection)
    try:
        await adjust_trust_for_rejection(
            user_id=author.created_by_user_id,
            entity_type="author",
            entity_id=author.id,
            reason=reason,
            is_book=False,
        )
    except Exception as e:
        # Log but don't fail the rejection
        logger.warning("Failed to adjust trust score: %s", e)

    # Refresh with books relationship loaded to avoid lazy load issues
    query = (
        select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    )
    result = await db.execute(query)
    author = result.scalar_one()

    # Invalidate caches (status changed) - pass book_ids to avoid DB query
    book_ids = [book.id for book in author.books] if author.books else []
    await cache.invalidate_author(author_id, r, book_ids=book_ids)

    return AuthorDetail.model_validate(author)
