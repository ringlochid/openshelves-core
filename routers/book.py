"""
Book router with wiki-style workflow, RBAC, and advanced search.
Implements Phase 3 book and review management endpoints.
"""

import logging
from datetime import datetime, timezone, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, and_, or_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_async_db
from dependencies.auth import (
    get_current_user,
    get_current_user_optional,
    require_scope,
    require_min_trust,
)
from models import (
    Book,
    Author,
    Review,
    BookSubscription,
    ReviewVote,
    ContentStatus,
    VoteType,
    EditHistory,
)
from schemas.book import (
    BookCreate,
    BookReplace,
    BookUpdate,
    BookDetail,
    BookListItem,
    BookListResponse,
    PaginatedBooksCursor,
    BookSortControl,
    SortField,
    SortDirection,
    RollbackRequest,
    ReviewVoteResponse,
)
from schemas.review import ReviewCreate, ReviewUpdate, ReviewVoteCreate
from schemas.shared import ReviewRead
from helpers.edit_history import (
    check_version_conflict,
    record_create,
    record_update,
    record_delete,
    record_approval,
    record_rejection,
    serialize_entity,
)
from helpers.cursor import encode_cursor, decode_cursor
from helpers.jury import clear_jury_votes
from services.auth_client import (
    adjust_trust_for_approval,
    adjust_trust_for_rejection,
    adjust_trust_for_review,
)
import cache
from cache import Redis, get_redis
from helpers.request import get_request_ip
from settings import settings

router = APIRouter(prefix="/books", tags=["Books"])

logger = logging.getLogger(__name__)
# ========================================
# PUBLIC ENDPOINTS (No Auth Required)
# ========================================


# TODO: Add caching for popular pattern, e.g. trending books
@router.get("", response_model=PaginatedBooksCursor)
async def list_books(
    q: str | None = Query(None, description="Full-text search query"),
    title: str | None = Query(None, description="Exact title filter"),
    author_id: int | None = Query(None, description="Filter by author ID"),
    before: int | None = Query(None, description="Filter by year (before)"),
    after: int | None = Query(None, description="Filter by year (after)"),
    tags: list[str] | None = Query(None, description="Filter by tags"),
    exclude_tags: list[str] | None = Query(None, description="Exclude tags"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    sort: list[str] = Query(
        default=[], description="Sort like 'similarity:desc', 'title:asc'"
    ),
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    """
    List approved, public books with advanced search.

    - Full-text search with `q` parameter (uses PostgreSQL search_tsv)
    - Trigram similarity fallback if FTS finds nothing
    - Multi-field sorting (similarity, title, year, subscribers)
    - Cursor-based pagination for consistent results
    - No authentication required
    """
    rl_key = cache.make_rate_limit_key("books:list", ip or "unknown")
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
    # Parse sort parameters
    sort_controls = []
    if q and not sort:
        # Default to similarity sort when searching
        sort_controls.append(
            BookSortControl(
                sort_field=SortField.by_similarity, sort_direction=SortDirection.desc
            )
        )
    else:
        for s in sort:
            if ":" in s:
                field, direction = s.split(":", 1)
                try:
                    sort_controls.append(
                        BookSortControl(
                            sort_field=SortField(field),
                            sort_direction=SortDirection(direction),
                        )
                    )
                except ValueError:
                    continue

    # Base conditions
    base_conditions = and_(
        Book.status == ContentStatus.APPROVED,
        Book.is_public == True,
        Book.is_deleted == False,
    )

    # Build query
    stmt = select(Book).where(base_conditions).options(selectinload(Book.authors))

    # Apply filters
    if title:
        stmt = stmt.where(Book.title == title)
    if author_id:
        stmt = stmt.where(Book.authors.any(Author.id == author_id))
    if before:
        stmt = stmt.where(Book.year <= before)
    if after:
        stmt = stmt.where(Book.year >= after)
    if tags and exclude_tags:
        et_set = set(exclude_tags)
        for t in tags:
            if t in et_set:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tag cannot be both included and excluded. Or you'll see nothing.",
                )
    if tags:
        stmt = stmt.where(Book.tags.op("&&")(tags))
    if exclude_tags:
        stmt = stmt.where(~Book.tags.op("&&")(exclude_tags))
    # FTS Search Logic
    total_score = None
    if q:
        # 1. Full-Text Search (High Precision)
        # using setweight for ranking: title=A (1.0), description=C (0.2)
        # rank_filter handles threshold
        ts_query = func.plainto_tsquery("english", q)
        rank = func.ts_rank(Book.search_tsv, ts_query)
        title_sim = func.similarity(Book.title, q)
        author_sim = func.coalesce(func.max(func.similarity(Author.name, q)), 0.0)

        # Weighted scoring: 60% FTS, 25% title similarity, 15% author similarity
        total_score = 0.6 * rank + 0.25 * title_sim + 0.15 * author_sim

        # Try FTS first
        fts_stmt = (
            stmt.outerjoin(Book.authors)
            .group_by(Book.id)
            .add_columns(total_score.label("total_score"))
            .having(total_score > 0.01)  # Filter out irrelevant results
        )

        # Check if FTS returns results
        fts_count_result = await db.execute(
            select(func.count()).select_from(fts_stmt.subquery())
        )
        fts_count = fts_count_result.scalar()

        if fts_count == 0:
            # Fallback to pure trigram similarity (no FTS threshold)
            trigram_score = 0.6 * title_sim + 0.4 * author_sim
            total_score = trigram_score  # Ensure ordering/cursor use trigram score
            stmt = (
                stmt.outerjoin(Book.authors)
                .group_by(Book.id)
                .add_columns(trigram_score.label("total_score"))
                .having(trigram_score > 0.1)  # Lower threshold for trigram
            )
        else:
            stmt = fts_stmt

    # Apply sorting
    order_exprs = []
    primary_sort = sort_controls[0] if sort_controls else None
    by_similarity = (
        primary_sort
        and primary_sort.sort_field == SortField.by_similarity
        and total_score is not None
    )

    for sort_ctrl in sort_controls:
        if sort_ctrl.sort_field == SortField.by_similarity:
            if total_score is not None:
                col = total_score
            else:
                continue
        elif sort_ctrl.sort_field == SortField.by_title:
            col = Book.title
        elif sort_ctrl.sort_field == SortField.by_year:
            col = Book.year
        elif sort_ctrl.sort_field == SortField.by_average_rating:
            col = Book.average_rating
        elif sort_ctrl.sort_field == SortField.by_subscriber_count:
            col = Book.subscriber_count
        elif sort_ctrl.sort_field == SortField.by_view_count:
            col = Book.view_count
        elif sort_ctrl.sort_field == SortField.by_trending_score:
            col = Book.trending_score
        else:
            continue

        order_exprs.append(
            desc(col) if sort_ctrl.sort_direction == SortDirection.desc else asc(col)
        )

    # Always add ID as tiebreaker
    order_exprs.append(Book.id.asc())
    stmt = stmt.order_by(*order_exprs)

    # Handle cursor pagination
    if cursor and by_similarity:
        data = decode_cursor(cursor)
        last_score = data["score"]
        last_id = data["id"]

        # Use HAVING for similarity (uses aggregated author similarity)
        stmt = stmt.having(
            or_(
                total_score < last_score,
                and_(total_score == last_score, Book.id > last_id),
            )
        )

    # Fetch limit + 1 to detect next page
    stmt = stmt.limit(limit + 1)
    result = await db.execute(stmt)
    rows = result.unique().all()

    # Separate items from pagination check
    items_rows = rows[:limit]
    has_next = len(rows) > limit

    books = [r[0] for r in items_rows]

    # Generate next cursor
    next_cursor = None
    if has_next and by_similarity and items_rows:
        last_row = items_rows[-1]
        last_book: Book = last_row[0]
        last_total_score = last_row[-1]  # total_score is last selected column

        next_cursor = encode_cursor(
            {"score": float(last_total_score), "id": last_book.id}
        )

    return PaginatedBooksCursor(
        items=[BookListItem.model_validate(b) for b in books],
        next_cursor=next_cursor,
    )


@router.get("/me", response_model=BookListResponse)
async def get_my_books(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    """
    Get all books created by the current user.
    Shows all statuses (PENDING, APPROVED, REJECTED) for owner management.
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "books:me", current_user.get("user_id") or "unknown"
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

    # Build query for user's books (any status, not deleted)
    query = (
        select(Book)
        .where(
            and_(
                Book.created_by_user_id == user_id,
                Book.is_deleted == False,
            )
        )
        .options(selectinload(Book.authors))
        .order_by(Book.created_at.desc())
    )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    books = result.scalars().all()

    return BookListResponse(
        items=[BookListItem.model_validate(b) for b in books],
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page,
    )


@router.get("/{book_id}", response_model=BookDetail)
async def get_book(
    book_id: int,
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
    current_user: dict | None = Depends(get_current_user_optional),
):
    """
    Get detailed book information.
    Approved, public books visible to everyone.
    Owners can see their own pending/non-public books.
    """
    rl_key = cache.make_rate_limit_key("books:get", ip or "unknown")
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
    cached = await cache.get_book(book_id, r)
    if (
        cached
        and cached.get("status") == "APPROVED"
        and cached.get("is_public") is True
    ):
        return cached

    # Query without status filter first to check ownership
    query = (
        select(Book)
        .where(
            and_(
                Book.id == book_id,
                Book.is_deleted == False,
            )
        )
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
    )

    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    # Access control
    is_owner = current_user and str(book.created_by_user_id) == current_user.get(
        "user_id"
    )
    is_public_approved = book.status == ContentStatus.APPROVED and book.is_public

    if not is_public_approved and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    # Track view (only for public books)
    if is_public_approved:
        viewer_id = ip or "anonymous"
        await cache.track_book_view(book_id, viewer_id, r)

        # Cache the result
        book_dict = BookDetail.model_validate(book).model_dump(mode="json")
        await cache.cache_book(book_id, book_dict, r)

    return BookDetail.model_validate(book)


@router.get("/{book_id}/reviews", response_model=list[ReviewRead])
async def get_book_reviews(
    book_id: int,
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Get all reviews for a book, ordered by helpfulness.
    Returns empty list if book not found (not 404).

    Cached for 5 minutes. Invalidated when:
    - Review is created, updated, or deleted
    - Review votes change (helpful/unhelpful counts)
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key("reviews:get", ip or "unknown")
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
    # Try cache first
    cached = await cache.get_reviews(book_id, r)
    if cached is not None:
        return [ReviewRead.model_validate(review) for review in cached]

    # Query database
    query = (
        select(Review)
        .where(
            and_(
                Review.book_id == book_id,
                Review.is_deleted == False,
            )
        )
        .order_by(Review.helpful_count.desc(), Review.created_at.desc())
    )

    result = await db.execute(query)
    reviews = result.scalars().all()

    # Cache the result
    reviews_data = [
        ReviewRead.model_validate(r).model_dump(mode="json") for r in reviews
    ]
    await cache.cache_reviews(book_id, reviews_data, r)

    return [ReviewRead.model_validate(r) for r in reviews]


# ========================================
# AUTHENTICATED ENDPOINTS
# ========================================


@router.post("", response_model=BookDetail, status_code=status.HTTP_201_CREATED)
async def create_book(
    data: BookCreate,
    current_user: dict = Depends(require_scope("books:draft")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Create a new book submission (PENDING status).

    Direct publish detection:
    - If user has `books:publish_direct` scope: APPROVED + is_public=True
    - Otherwise: PENDING + is_public=False (enters jury queue)
    """
    rl_key = cache.make_rate_limit_key(
        "books:create", current_user.get("user_id") or "unknown"
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

    # Check for direct publish privilege
    user_scopes = current_user.get("scopes", [])
    has_direct_publish = "books:publish_direct" in user_scopes

    # Validate authors exist and are approved
    author_objs = []
    if data.author_ids:
        author_query = select(Author).where(
            and_(
                Author.id.in_(data.author_ids),
                Author.status == ContentStatus.APPROVED,
                Author.is_deleted == False,
            )
        )
        result = await db.execute(author_query)
        author_objs = list(result.scalars().all())

        if len(author_objs) != len(data.author_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Some author IDs are invalid or not approved",
            )

    # Create book (cover_key/file_key/file_format set via upload pipeline)
    book = Book(
        title=data.title,
        year=data.year,
        description=data.description,
        tags=data.tags if data.tags else [],
        created_by_user_id=current_user["user_id"],
        status=ContentStatus.APPROVED if has_direct_publish else ContentStatus.PENDING,
        is_public=has_direct_publish,
        version=1,
    )

    book.authors = author_objs
    db.add(book)
    await db.flush()  # Get book.id before history recording

    # Record creation in edit history
    await db.refresh(book, ["authors"])  # Load relationship for serialization
    await record_create(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=current_user["user_id"],
        data=serialize_entity(book),
    )

    await db.commit()
    await db.refresh(book, ["authors", "reviews"])

    # Cache the newly created book for immediate reads
    book_dict = BookDetail.model_validate(book).model_dump(mode="json")
    await cache.cache_book(book.id, book_dict, r)

    # Invalidate list caches and author caches
    author_ids = [author.id for author in author_objs]
    await cache.invalidate_book(book.id, r, author_ids=author_ids)

    return BookDetail.model_validate(book)


@router.put("/{book_id}", response_model=BookDetail)
async def replace_book(
    book_id: int,
    data: BookReplace,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Replace an existing book with new data.
    """
    rl_key = cache.make_rate_limit_key(
        "books:replace", current_user.get("user_id") or "unknown"
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

    query = (
        select(Book)
        .where(Book.id == book_id)
        .options(selectinload(Book.authors))
        .options(selectinload(Book.reviews))
    )
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    # Check if deleted
    if book.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Book has been deleted"
        )

    # Permission check
    user_id = current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    is_owner = book.created_by_user_id == user_id
    has_update_own = "books:update_own" in user_scopes
    is_wiki_editor = (
        "books:edit_public_meta" in user_scopes
        and book.status == ContentStatus.APPROVED
    )

    if is_owner and has_update_own:
        # Owner can update their own book
        pass
    elif is_wiki_editor:
        # Wiki editor can update any APPROVED book
        pass
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions. Owner needs 'books:update_own' or wiki-editor needs 'books:edit_public_meta' (APPROVED only)",
        )

    # Version conflict check
    check_version_conflict(book.version, data.version, "book", book_id)

    old_version = book.version
    await db.refresh(book, ["authors"])  # Load relationship for serialization
    old_data = serialize_entity(book)

    # replace book
    book.title = data.title
    book.year = data.year
    book.description = data.description
    book.tags = data.tags
    # Note: cover_key/file_key/file_format not settable via PUT - use upload endpoints
    book.version = old_version + 1

    book.last_edited_by = user_id
    book.last_edited_at = datetime.now(timezone.utc)
    book.updated_at = datetime.now(timezone.utc)

    # Update authors if provided
    author_objs = []
    affected_author_ids = [author.id for author in book.authors]
    if data.author_ids is not None:
        author_query = select(Author).where(
            and_(
                Author.id.in_(data.author_ids),
                Author.status == ContentStatus.APPROVED,
                Author.is_deleted == False,
            )
        )
        result = await db.execute(author_query)
        author_objs = list(result.scalars().all())

        if len(author_objs) != len(data.author_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Some author IDs are invalid or not approved",
            )

    book.authors = author_objs
    affected_author_ids.extend([author.id for author in author_objs])

    # Record update in history
    await db.refresh(book, ["authors"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=user_id,
        old_data=old_data,
        new_data=serialize_entity(book),
        new_version=book.version,
        old_version=old_version,
    )

    await db.commit()
    await db.refresh(book)

    await cache.invalidate_book(book.id, r, author_ids=affected_author_ids)
    return BookDetail.model_validate(book)


@router.patch("/{book_id}", response_model=BookDetail)
async def update_book(
    book_id: int,
    data: BookUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Update book metadata with optimistic locking.

    Permissions:
    - Owner: if book is PENDING
    - Contributor+: if book is APPROVED (wiki-style editing with books:edit_public_meta)
    """
    rl_key = cache.make_rate_limit_key(
        "books:update", current_user.get("user_id") or "unknown"
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

    # Load book with relationships
    query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    # Check if deleted
    if book.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Book has been deleted"
        )

    # Permission check
    user_id = current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    is_owner = book.created_by_user_id == user_id
    has_update_own = "books:update_own" in user_scopes
    is_wiki_editor = (
        "books:edit_public_meta" in user_scopes
        and book.status == ContentStatus.APPROVED
    )

    # Permission logic:
    # 1. Owner with books:update_own → can update own book (pending or approved)
    # 2. Non-owner with books:edit_public_meta → can update ANY APPROVED book (wiki mode)
    if is_owner and has_update_own:
        # Owner can update their own book
        pass
    elif is_wiki_editor:
        # Wiki editor can update any APPROVED book
        pass
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions. Owner needs 'books:update_own' or wiki-editor needs 'books:edit_public_meta' (APPROVED only)",
        )

    # Version conflict check
    check_version_conflict(book.version, data.version, "book", book_id)

    # Capture OLD state and author IDs for cache invalidation
    await db.refresh(book, ["authors"])  # Load relationship for serialization
    old_data = serialize_entity(book)
    previous_author_ids = {author.id for author in book.authors}

    # Apply updates
    if data.title is not None:
        book.title = data.title
    if data.year is not None:
        book.year = data.year
    if data.description is not None:
        book.description = data.description
    if data.tags is not None:
        book.tags = data.tags
    # Note: cover_key/file_key/file_format not settable via PATCH - use upload endpoints

    # Update authors if provided
    if data.author_ids is not None:
        author_query = select(Author).where(
            and_(
                Author.id.in_(data.author_ids),
                Author.status == ContentStatus.APPROVED,
                Author.is_deleted == False,
            )
        )
        result = await db.execute(author_query)
        author_objs = list(result.scalars().all())

        if len(author_objs) != len(data.author_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Some author IDs are invalid or not approved",
            )

        book.authors = author_objs

    # Increment version and track editor
    old_version = book.version
    book.version += 1
    book.last_edited_by = user_id
    book.last_edited_at = datetime.now(timezone.utc)

    # Record update in history
    await db.refresh(book, ["authors"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=user_id,
        old_data=old_data,
        new_data=serialize_entity(book),
        new_version=book.version,
        old_version=old_version,
    )

    await db.commit()
    await db.refresh(book, ["authors", "reviews"])

    # Invalidate caches (OLD ∪ NEW author IDs)
    new_author_ids = {author.id for author in book.authors}
    affected_author_ids = list(previous_author_ids | new_author_ids)
    await cache.invalidate_book(book_id, r, author_ids=affected_author_ids)

    return BookDetail.model_validate(book)


@router.delete("/{book_id}/own", status_code=status.HTTP_204_NO_CONTENT)
async def delete_own_book(
    book_id: int,
    current_user: dict = Depends(require_scope("books:delete_own")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Delete own book (owner only).
    Soft-deletes the book by setting is_deleted=True.
    Requires 'books:delete_own' scope + ownership.
    """
    rl_key = cache.make_rate_limit_key(
        "books:delete", current_user.get("user_id") or "unknown"
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

    # Fetch book with authors
    query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    # Check ownership
    if book.created_by_user_id != current_user["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own books",
        )

    if book.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Book is already deleted"
        )

    # Save old data for history
    old_data = serialize_entity(book)
    old_version = book.version

    # Soft delete
    book.is_deleted = True
    book.deleted_at = datetime.now(timezone.utc)
    book.is_public = False
    book.version += 1
    book.last_edited_by = current_user["user_id"]
    book.last_edited_at = datetime.now(timezone.utc)

    # Record deletion in edit history
    await record_delete(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=current_user["user_id"],
        data=old_data,
        version=old_version,
    )

    await db.commit()

    # Invalidate caches - pass author_ids to avoid DB query
    author_ids = [author.id for author in book.authors] if book.authors else []
    await cache.invalidate_book(book_id, r, author_ids=author_ids)


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(
    book_id: int,
    current_user: dict = Depends(require_scope("content:takedown")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Soft delete a book (curator/admin only).
    Can be recovered within 24 hours.
    """
    rl_key = cache.make_rate_limit_key(
        "books:delete", current_user.get("user_id") or "unknown"
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

    query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    if book.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Book is already deleted"
        )

    # Save old data for history
    old_data = serialize_entity(book)
    old_version = book.version

    # Soft delete
    book.is_deleted = True
    book.deleted_at = datetime.now(timezone.utc)
    book.is_public = False
    book.version += 1

    # Record deletion in history
    await record_delete(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=current_user["user_id"],
        data=old_data,
        version=old_version,
    )

    await db.commit()

    # Invalidate caches
    author_ids = [author.id for author in book.authors]
    await cache.invalidate_book(book_id, r, author_ids=author_ids)


@router.post("/{book_id}/rollback", response_model=BookDetail)
async def rollback_book_version(
    book_id: int,
    data: RollbackRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Rollback book to a previous version from edit history.

    Permissions: owner (if PENDING) or wiki-editor (if APPROVED)
    """
    rl_key = cache.make_rate_limit_key(
        "books:rollback", current_user.get("user_id") or "unknown"
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

    # Load book
    query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    # Permission check
    user_id = current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    is_owner = book.created_by_user_id == user_id and "books:update_own" in user_scopes
    is_wiki_editor = (
        "books:edit_public_meta" in user_scopes
        and book.status == ContentStatus.APPROVED
    )

    if not (is_owner or is_wiki_editor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to rollback this book",
        )

    # Optimistic locking: check caller's version matches current version
    check_version_conflict(book.version, data.version, "book", book_id)

    # Find target version in history
    history_query = select(EditHistory).where(
        and_(
            EditHistory.entity_type == "book",
            EditHistory.entity_id == book_id,
            EditHistory.version == data.target_version,
        )
    )
    history_result = await db.execute(history_query)
    history_record = history_result.scalar_one_or_none()

    if not history_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {data.target_version} not found in history",
        )

    # Check version conflict (cannot rollback forward)
    if data.target_version >= book.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot rollback to version {data.target_version} (current: {book.version})",
        )

    # Capture OLD state and author IDs
    old_data = serialize_entity(book)
    previous_author_ids = {author.id for author in book.authors}

    # Restore data from history
    restored_data = history_record.new_data
    restored_changes = history_record.changes
    if not restored_changes or restored_changes.get("total_changes") == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Version {data.target_version} has no changes to restore",
        )
    if not restored_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Version {data.target_version} has no data to restore",
        )

    book.title = restored_data.get("title", book.title)
    book.year = restored_data.get("year")
    book.description = restored_data.get("description")
    book.tags = restored_data.get("tags", [])
    book.cover_key = restored_data.get("cover_key")
    book.file_key = restored_data.get("file_key")
    book.file_format = restored_data.get("file_format")

    # Restore authors (handle deleted authors gracefully)
    restored_author_ids = restored_data.get("author_ids", [])
    if restored_author_ids:
        author_query = select(Author).where(
            and_(
                Author.id.in_(restored_author_ids),
                Author.is_deleted == False,
            )
        )
        result = await db.execute(author_query)
        author_objs = list(result.scalars().all())

        # Warn if some authors are missing
        found_ids = {a.id for a in author_objs}
        missing_ids = set(restored_author_ids) - found_ids
        if missing_ids:
            logger.warning("Rollback skipped deleted authors: %s", missing_ids)

        book.authors = author_objs

    # Increment version
    old_version = book.version
    book.version += 1
    book.last_edited_by = user_id
    book.last_edited_at = datetime.now(timezone.utc)

    # Record rollback in history
    await record_update(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=user_id,
        old_data=old_data,
        new_data=serialize_entity(book),
        new_version=book.version,
        old_version=old_version,
    )

    await db.commit()
    await db.refresh(book, ["authors", "reviews"])

    # Invalidate caches (OLD ∪ NEW author IDs)
    new_author_ids = {author.id for author in book.authors}
    affected_author_ids = list(previous_author_ids | new_author_ids)
    await cache.invalidate_book(book_id, r, author_ids=affected_author_ids)

    return BookDetail.model_validate(book)


@router.post("/{book_id}/recover", response_model=BookDetail)
async def recover_deleted_book(
    book_id: int,
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Recover a soft-deleted book (curator only).
    Only works within 24 hours of deletion.
    """
    rl_key = cache.make_rate_limit_key(
        "books:recover", current_user.get("user_id") or "unknown"
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

    query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    if not book.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Book is not deleted"
        )

    # Check 24-hour window
    if book.deleted_at:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        if book.deleted_at < cutoff:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Book deleted more than 24 hours ago, cannot recover",
            )

    # Save old state
    await db.refresh(book, ["authors"])  # Load relationship for serialization
    old_data = serialize_entity(book)
    old_version = book.version

    # Recover
    book.is_deleted = False
    book.deleted_at = None
    book.is_public = (
        book.status == ContentStatus.APPROVED
    )  # Restore visibility based on approval
    book.version += 1

    # Record recovery in history
    await db.refresh(book, ["authors"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(book),
        new_version=book.version,
        old_version=old_version,
    )

    await db.commit()
    await db.refresh(book, ["authors", "reviews"])

    # Invalidate caches
    author_ids = [author.id for author in book.authors]
    await cache.invalidate_book(book_id, r, author_ids=author_ids)

    return BookDetail.model_validate(book)


# ========================================
# CURATOR/ADMIN ENDPOINTS
# ========================================


@router.post("/{book_id}/approve", response_model=BookDetail)
async def approve_book(
    book_id: int,
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Curator instant approval (+20 trust to submitter).
    Books get doubled trust reward due to file upload validation.
    """
    rl_key = cache.make_rate_limit_key(
        "books:approve", current_user.get("user_id") or "unknown"
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

    query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    if book.status == ContentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Book is already approved"
        )

    # Save old state
    await db.refresh(
        book, ["authors"]
    )  # Load relationship for serialization (may already be loaded)
    old_data = serialize_entity(book)
    old_version = book.version

    # Approve
    book.status = ContentStatus.APPROVED
    book.is_public = True
    book.version += 1

    # Record approval in history
    await db.refresh(book, ["authors"])  # Refresh after changes for new_data
    await record_approval(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(book),
        new_version=book.version,
        old_version=old_version,
    )

    # Clear any pending jury votes
    await clear_jury_votes(db, "book", book.id)

    await db.commit()
    await db.refresh(book, ["authors", "reviews"])

    # Award trust to submitter (+20 for books, doubled reward)
    try:
        await adjust_trust_for_approval(
            user_id=book.created_by_user_id,
            entity_type="book",
            entity_id=book.id,
            is_book=True,
        )
    except Exception as e:
        logger.warning("Failed to adjust trust for book approval: %s", e)

    # Invalidate caches
    author_ids = [author.id for author in book.authors]
    await cache.invalidate_book(book_id, r, author_ids=author_ids)

    return BookDetail.model_validate(book)


@router.post("/{book_id}/reject", response_model=BookDetail)
async def reject_book(
    book_id: int,
    reason: str = Query(..., description="Rejection reason"),
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Curator instant rejection (-10 trust to submitter).
    Books get doubled trust penalty due to file upload validation.
    """
    rl_key = cache.make_rate_limit_key(
        "books:reject", current_user.get("user_id") or "unknown"
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

    query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )

    if book.status == ContentStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Book is already rejected"
        )

    # Save old state
    await db.refresh(
        book, ["authors"]
    )  # Load relationship for serialization (may already be loaded)
    old_data = serialize_entity(book)
    old_version = book.version

    # Reject
    book.status = ContentStatus.REJECTED
    book.is_public = False
    book.version += 1

    # Record rejection in history
    await db.refresh(book, ["authors"])  # Refresh after changes for new_data
    await record_rejection(
        db=db,
        entity_type="book",
        entity_id=book.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(book),
        new_version=book.version,
        old_version=old_version,
    )

    # Clear any pending jury votes
    await clear_jury_votes(db, "book", book.id)

    await db.commit()
    await db.refresh(book, ["authors", "reviews"])

    # Penalize submitter (-10 for books, doubled penalty)
    try:
        await adjust_trust_for_rejection(
            user_id=book.created_by_user_id,
            entity_type="book",
            entity_id=book.id,
            reason=reason,
            is_book=True,
        )
    except Exception as e:
        logger.warning("Failed to adjust trust for book rejection: %s", e)

    # Invalidate caches
    author_ids = [author.id for author in book.authors]
    await cache.invalidate_book(book_id, r, author_ids=author_ids)

    return BookDetail.model_validate(book)


# ========================================
# SOCIAL ENDPOINTS
# ========================================


@router.post("/{book_id}/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe_to_book(
    book_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Subscribe to book updates (NO trust reward).
    Social engagement is for tracking only.
    """
    rl_key = cache.make_rate_limit_key(
        "books:subscribe", current_user.get("user_id") or "unknown"
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

    # Verify book exists and is approved
    query = select(Book).where(
        and_(
            Book.id == book_id,
            Book.status == ContentStatus.APPROVED,
            Book.is_public == True,
            Book.is_deleted == False,
        )
    )
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found or not approved",
        )

    # Check if already subscribed
    existing = await db.execute(
        select(BookSubscription).where(
            and_(
                BookSubscription.user_id == current_user["user_id"],
                BookSubscription.book_id == book_id,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already subscribed to this book",
        )

    # Create subscription
    subscription = BookSubscription(
        user_id=current_user["user_id"],
        book_id=book_id,
    )
    db.add(subscription)

    # Increment subscriber count
    book.subscriber_count += 1

    await db.commit()

    # Invalidate cache
    await cache.invalidate_book(book_id, r)

    # NOTE: NO trust reward - prevents subscribe/unsubscribe exploit loop


@router.delete("/{book_id}/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_from_book(
    book_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Unsubscribe from book updates.
    Decrements subscriber count.
    """
    rl_key = cache.make_rate_limit_key(
        "books:unsubscribe", current_user.get("user_id") or "unknown"
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

    # Find subscription
    query = select(BookSubscription).where(
        and_(
            BookSubscription.user_id == current_user["user_id"],
            BookSubscription.book_id == book_id,
        )
    )
    result = await db.execute(query)
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not subscribed to this book",
        )

    # Get book to decrement count
    book_query = select(Book).where(Book.id == book_id)
    book_result = await db.execute(book_query)
    book = book_result.scalar_one_or_none()

    if book:
        book.subscriber_count = max(0, book.subscriber_count - 1)

    # Delete subscription
    await db.delete(subscription)
    await db.commit()

    # Invalidate cache
    if book:
        await cache.invalidate_book(book_id, r)


# ========================================
# REVIEW ENDPOINTS
# ========================================


@router.post(
    "/{book_id}/reviews", response_model=ReviewRead, status_code=status.HTTP_201_CREATED
)
async def create_review(
    book_id: int,
    data: ReviewCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Create a review for an approved book.
    One review per user per book (unique constraint).
    """
    rl_key = cache.make_rate_limit_key(
        "reviews:create", current_user.get("user_id") or "unknown"
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

    # Verify book exists and is approved (load authors for cache invalidation)
    book_query = (
        select(Book)
        .where(
            and_(
                Book.id == book_id,
                Book.status == ContentStatus.APPROVED,
                Book.is_public == True,
                Book.is_deleted == False,
            )
        )
        .options(selectinload(Book.authors))
    )
    book_result = await db.execute(book_query)
    book = book_result.scalar_one_or_none()

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found or not approved",
        )

    # Check for duplicate review (unique constraint)
    user_id = current_user["user_id"]  # Already UUID from auth dependency
    existing = await db.execute(
        select(Review).where(
            and_(
                Review.book_id == book_id,
                Review.user_id == user_id,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already reviewed this book",
        )

    # Note: data.book_id from schema is ignored, using book_id from path
    # Create review
    review = Review(
        book_id=book_id,  # Use path parameter
        user_id=user_id,
        rating=data.rating,
        comment=data.comment,
        helpful_count=0,
        unhelpful_count=0,
        trust_awarded=0,
    )

    db.add(review)
    await db.commit()
    await db.refresh(review)

    # Invalidate book cache (with author IDs for cascading)
    author_ids = [author.id for author in book.authors]
    await cache.invalidate_book(book_id, r, author_ids=author_ids)

    return ReviewRead.model_validate(review)


@router.patch("/reviews/{review_id}", response_model=ReviewRead)
async def update_review(
    review_id: int,
    data: ReviewUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Update your own review.
    Only rating and comment can be updated.
    """
    rl_key = cache.make_rate_limit_key(
        "reviews:update", current_user.get("user_id") or "unknown"
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

    query = select(Review).where(Review.id == review_id)
    result = await db.execute(query)
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review not found"
        )

    # Check ownership
    if review.user_id != current_user["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to edit this review",
        )

    # Apply updates
    if data.rating is not None:
        review.rating = data.rating
    if data.comment is not None:
        review.comment = data.comment

    review.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(review)

    # Invalidate book cache
    await cache.invalidate_book(review.book_id, r)

    return ReviewRead.model_validate(review)


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    review_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Soft delete your own review.
    Curators can delete any review with content:takedownscope.
    """
    rl_key = cache.make_rate_limit_key(
        "reviews:delete", current_user.get("user_id") or "unknown"
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

    query = select(Review).where(Review.id == review_id)
    result = await db.execute(query)
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review not found"
        )

    # Permission check
    user_scopes = current_user.get("scopes", [])
    is_owner = review.user_id == current_user["user_id"]
    can_delete_any = "content:takedown" in user_scopes

    if not (is_owner or can_delete_any):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this review",
        )

    # Soft delete
    review.is_deleted = True
    review.deleted_at = datetime.now(timezone.utc)

    await db.commit()

    # Invalidate book cache
    await cache.invalidate_book(review.book_id, r)


@router.post("/reviews/{review_id}/vote", response_model=ReviewVoteResponse)
async def vote_on_review(
    review_id: int,
    data: ReviewVoteCreate,
    current_user: dict = Depends(require_min_trust(50)),  # Trusted+ users only
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Vote on review helpfulness (trusted+ users only).

    Awards trust to reviewer:
    - Helpful: +1 trust
    - Unhelpful: -1 trust
    - Max ±5 trust per review
    """
    rl_key = cache.make_rate_limit_key(
        "reviews:vote", current_user.get("user_id") or "unknown"
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

    # Load review
    query = select(Review).where(Review.id == review_id)
    result = await db.execute(query)
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review not found"
        )

    if review.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Review has been deleted"
        )

    # Check if already voted
    user_id = current_user["user_id"]  # Already UUID from auth dependency
    existing_vote = await db.execute(
        select(ReviewVote).where(
            and_(
                ReviewVote.review_id == review_id,
                ReviewVote.user_id == user_id,
            )
        )
    )
    existing = existing_vote.scalar_one_or_none()

    # Store old trust value for calculating actual change
    old_trust_awarded = review.trust_awarded

    if existing:
        # Vote change logic
        if existing.vote == data.vote:
            return ReviewVoteResponse(
                message="Already voted",
                helpful_count=review.helpful_count,
                unhelpful_count=review.unhelpful_count,
                trust_delta=0,
            )

        # Change vote: reverse previous and apply new
        if existing.vote == VoteType.HELPFUL:
            review.helpful_count -= 1
            review.unhelpful_count += 1
        else:
            review.unhelpful_count -= 1
            review.helpful_count += 1

        existing.vote = data.vote  # VoteType enum from schema
    else:
        # New vote (data.vote is already VoteType from schema)
        vote = ReviewVote(
            user_id=user_id,
            review_id=review_id,
            vote=data.vote,
        )
        db.add(vote)

        if data.vote == VoteType.HELPFUL:
            review.helpful_count += 1
        else:
            review.unhelpful_count += 1

    # Commit vote count changes first
    await db.commit()
    await db.refresh(review)

    # Calculate trust from vote balance (helpful - unhelpful), capped at ±5
    new_trust_awarded = max(-5, min(5, review.helpful_count - review.unhelpful_count))
    delta = new_trust_awarded - old_trust_awarded

    # Update trust_awarded if it changed
    if delta != 0:
        review.trust_awarded = new_trust_awarded
        await db.commit()
        await db.refresh(review)

        # Award trust to reviewer
        try:
            await adjust_trust_for_review(
                user_id=review.user_id,
                delta=delta,
            )
        except Exception as e:
            logger.warning("Failed to adjust reviewer trust: %s", e)

    # Invalidate book cache
    await cache.invalidate_book(review.book_id, r)

    return ReviewVoteResponse(
        message="Vote recorded",
        helpful_count=review.helpful_count,
        unhelpful_count=review.unhelpful_count,
        trust_delta=delta,
    )


@router.delete("/reviews/{review_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def remove_review_vote(
    review_id: int,
    current_user: dict = Depends(require_min_trust(50)),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Remove your vote from a review.
    Reverses the trust adjustment.
    """
    rl_key = cache.make_rate_limit_key(
        "reviews:unvote", current_user.get("user_id") or "unknown"
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

    # Find vote
    user_id = current_user["user_id"]  # Already UUID from auth dependency
    query = select(ReviewVote).where(
        and_(
            ReviewVote.review_id == review_id,
            ReviewVote.user_id == user_id,
        )
    )
    result = await db.execute(query)
    vote = result.scalar_one_or_none()

    if not vote:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have not voted on this review",
        )

    # Load review
    review_query = select(Review).where(Review.id == review_id)
    review_result = await db.execute(review_query)
    review = review_result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review not found"
        )

    # Store old trust value for calculating actual change
    old_trust_awarded = review.trust_awarded

    # Reverse vote count
    if vote.vote == VoteType.HELPFUL:
        review.helpful_count = max(0, review.helpful_count - 1)
    else:
        review.unhelpful_count = max(0, review.unhelpful_count - 1)

    # Delete vote
    await db.delete(vote)

    # Commit vote removal first
    await db.commit()
    await db.refresh(review)

    # Recalculate trust from vote balance (helpful - unhelpful), capped at ±5
    new_trust_awarded = max(-5, min(5, review.helpful_count - review.unhelpful_count))
    delta = new_trust_awarded - old_trust_awarded

    # Update trust_awarded if it changed
    if delta != 0:
        review.trust_awarded = new_trust_awarded
        await db.commit()
        await db.refresh(review)

        # Adjust reviewer trust
        try:
            await adjust_trust_for_review(
                user_id=review.user_id,
                delta=delta,
            )
        except Exception as e:
            logger.warning("Failed to reverse reviewer trust: %s", e)

    # Invalidate book cache
    await cache.invalidate_book(review.book_id, r)
