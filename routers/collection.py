"""
Collection router for Library Service.
Supports curated book collections with workflow, versioning, and ordered books.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, asc, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import cache
from cache import Redis
from database import get_async_db
from dependencies.auth import get_current_user, get_current_user_optional, require_scope
from helpers.request import get_request_ip
from helpers.edit_history import (
    record_approval,
    record_create,
    record_delete,
    record_recovery,
    record_rejection,
    record_update,
    serialize_entity,
)
from helpers.cursor import encode_cursor, decode_cursor
from helpers.jury import clear_jury_votes
from helpers.collection import (
    check_collection_permission,
    check_delete_permission,
    link_books_to_collection,
)
from models import (
    Book,
    Collection,
    CollectionBook,
    CollectionSubscription,
    ContentStatus,
    EditHistory,
)
from schemas.collection import (
    CollectionSortField,
    CollectionSortControl,
    CollectionBookAdd,
    CollectionBookRead,
    CollectionBookUpdate,
    CollectionCreate,
    CollectionDetail,
    CollectionListItem,
    CollectionRead,
    CollectionRollbackRequest,
    CollectionUpdate,
    CollectionListResponse,
    PaginatedCollectionsCursor,
    CollectionBookAddResponse,
    CollectionBookReorderResponse,
    SubscriptionResponse,
)
from schemas.shared import SortDirection
from services.auth_client import adjust_trust_for_approval, adjust_trust_for_rejection
from settings import settings


router = APIRouter(prefix="/collections", tags=["Collections"])

logger = logging.getLogger(__name__)
# ========================================
# PUBLIC ENDPOINTS
# ========================================


# TODO: Add caching for popular pattern, e.g. trending collections
@router.get("", response_model=PaginatedCollectionsCursor)
async def list_collections(
    q: str | None = Query(None, description="Full-text search query"),
    name: str | None = Query(None, description="Name similarity search"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    sort: list[str] = Query(
        default=[], description="Sort like 'similarity:desc', 'trending_score:desc'"
    ),
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    List approved, public collections with search and cursor pagination.
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key("collections:list", ip or "unknown")
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
        Collection.status == ContentStatus.APPROVED,
        Collection.is_public == True,
        Collection.is_deleted == False,
    )

    # Build query
    stmt = (
        select(Collection)
        .options(selectinload(Collection.books))
        .where(base_conditions)
    )

    # Parse sort controls
    sort_controls = []
    if q and not sort:
        sort_controls.append(
            CollectionSortControl(
                sort_field=CollectionSortField.by_similarity,
                sort_direction=SortDirection.desc,
            )
        )
    else:
        for s in sort:
            if ":" in s:
                field, direction = s.split(":", 1)
                try:
                    sort_controls.append(
                        CollectionSortControl(
                            sort_field=CollectionSortField(field),
                            sort_direction=SortDirection(direction),
                        )
                    )
                except ValueError:
                    continue

    # FTS/Trigram Search
    total_score = None
    if q:
        ts_query = func.plainto_tsquery("english", q)
        rank = func.ts_rank(Collection.search_tsv, ts_query)
        name_sim = func.similarity(Collection.name, q)
        book_sim = func.coalesce(func.max(func.similarity(Book.title, q)), 0.0)

        # Weighted: 60% FTS, 25% name similarity, 15% book similarity
        total_score = 0.6 * rank + 0.25 * name_sim + 0.15 * book_sim

        # Try FTS first
        fts_stmt = (
            stmt.outerjoin(CollectionBook)
            .outerjoin(Book)
            .group_by(Collection.id)
            .add_columns(total_score.label("total_score"))
            .having(total_score > 0.01)
        )

        fts_count_result = await db.execute(
            select(func.count()).select_from(fts_stmt.subquery())
        )
        fts_count = fts_count_result.scalar()

        if fts_count == 0:
            # Fallback to trigram
            total_score = name_sim + book_sim
            stmt = (
                stmt.outerjoin(CollectionBook)
                .outerjoin(Book)
                .group_by(Collection.id)
                .add_columns(total_score.label("total_score"))
                .having(total_score > 0.1)
            )
        else:
            stmt = fts_stmt

    elif name:
        # Pure trigram search on name
        name_sim = func.similarity(Collection.name, name)
        stmt = stmt.add_columns(name_sim.label("total_score")).where(name_sim > 0.1)
        total_score = name_sim

    # Apply sorting
    order_exprs = []
    by_similarity = False
    primary_sort = sort_controls[0] if sort_controls else None
    if (
        primary_sort
        and primary_sort.sort_field == CollectionSortField.by_similarity
        and total_score is not None
    ):
        by_similarity = True

    for sort_ctl in sort_controls:
        if sort_ctl.sort_field == CollectionSortField.by_similarity:
            if total_score is not None:
                col = total_score
            else:
                continue
        elif sort_ctl.sort_field == CollectionSortField.by_name:
            col = Collection.name
        elif sort_ctl.sort_field == CollectionSortField.by_subscriber_count:
            col = Collection.subscriber_count
        elif sort_ctl.sort_field == CollectionSortField.by_view_count:
            col = Collection.view_count
        elif sort_ctl.sort_field == CollectionSortField.by_trending_score:
            col = Collection.trending_score
        else:
            continue

        order_exprs.append(
            desc(col) if sort_ctl.sort_direction == SortDirection.desc else asc(col)
        )

    # Tiebreaker
    order_exprs.append(Collection.id.asc())
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
                and_(total_score == last_score, Collection.id > last_id),
            )
        )

    # Fetch limit + 1 to detect next page
    stmt = stmt.limit(limit + 1)
    result = await db.execute(stmt)
    rows = result.unique().all()

    # Separate items from pagination check
    items_rows = rows[:limit]
    has_next = len(rows) > limit

    collections = [r[0] for r in items_rows]

    # Generate next cursor
    next_cursor = None
    if has_next and by_similarity and items_rows:
        last_row = items_rows[-1]
        last_collection: Collection = last_row[0]
        last_total_score = last_row[-1]  # total_score is last selected column

        next_cursor = encode_cursor(
            {"score": float(last_total_score), "id": last_collection.id}
        )

    return PaginatedCollectionsCursor(
        items=[CollectionListItem.model_validate(c) for c in collections],
        next_cursor=next_cursor,
    )


@router.get("/me", response_model=CollectionListResponse)
async def get_my_collections(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Get all collections created by the current user.
    Shows all statuses (PENDING, APPROVED, REJECTED) for owner management.
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "collections:me", str(current_user.get("user_id") or "unknown")
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

    # Build query for user's collections (any status, not deleted)
    query = (
        select(Collection)
        .where(
            and_(
                Collection.created_by_user_id == user_id,
                Collection.is_deleted == False,
            )
        )
        .order_by(Collection.created_at.desc())
    )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    # Execute
    result = await db.execute(query)
    collections = result.scalars().all()

    return CollectionListResponse(
        items=[CollectionRead.model_validate(c) for c in collections],
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page,
    )


@router.get("/{collection_id}", response_model=CollectionDetail)
async def get_collection(
    collection_id: int,
    ip: str | None = Depends(get_request_ip),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
    current_user: dict | None = Depends(get_current_user_optional),
):
    """
    Get detailed collection information with ordered books.
    Owner can see their own pending collections.
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key("collections:get", ip or "unknown")
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
    cached = await cache.get_collection(collection_id, r)
    if (
        cached
        and cached.get("status") == "APPROVED"
        and cached.get("is_public") is True
    ):
        return cached

    # Query with books eagerly loaded
    query = (
        select(Collection)
        .where(Collection.id == collection_id, Collection.is_deleted == False)
        .options(selectinload(Collection.books).selectinload(CollectionBook.book))
    )

    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Access control
    is_owner = current_user and collection.created_by_user_id == current_user.get(
        "user_id"
    )
    is_public_approved = (
        collection.status == ContentStatus.APPROVED and collection.is_public
    )

    if not is_public_approved and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Track view (only for public collections)
    if is_public_approved:
        viewer_id = ip or "anonymous"
        await cache.track_collection_view(collection_id, viewer_id, r)

        # Cache the result
        collection_dict = CollectionDetail.model_validate(collection).model_dump(
            mode="json"
        )
        await cache.cache_collection(collection_id, collection_dict, r)

    return CollectionDetail.model_validate(collection)


# ========================================
# AUTHENTICATED CRUD ENDPOINTS
# ========================================


@router.post("", response_model=CollectionRead, status_code=status.HTTP_201_CREATED)
async def create_collection(
    data: CollectionCreate,
    current_user: dict = Depends(require_scope("collections:create")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Create a new collection (pending status).
    Optionally link books using book_ids array (position = index + 1).
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "collections:create", str(current_user.get("user_id") or "unknown")
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

    # Check if user can publish directly (bypass jury)
    user_scopes = current_user.get("scopes", [])
    if "collections:publish_direct" in user_scopes:
        initial_status = ContentStatus.APPROVED
        initial_public = True
    else:
        initial_status = ContentStatus.PENDING
        initial_public = False

    # Create collection
    collection = Collection(
        name=data.name,
        description=data.description,
        # Note: cover_key set via upload pipeline, not create endpoint
        created_by_user_id=current_user["user_id"],
        status=initial_status,
        is_public=initial_public,
        version=1,
    )
    db.add(collection)
    await db.flush()

    # Link books if provided
    if data.book_ids:
        await link_books_to_collection(
            db, collection, data.book_ids, error_when_book_not_found=True
        )

    # Record creation in history
    await db.refresh(collection, ["books"])  # Load relationship for serialization
    await record_create(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        data=serialize_entity(collection),
    )

    await db.commit()
    await db.refresh(collection)

    # Invalidate caches
    await cache.invalidate_collection(collection.id, r)

    return CollectionRead.model_validate(collection)


@router.patch("/{collection_id}", response_model=CollectionRead)
async def update_collection(
    collection_id: int,
    data: CollectionUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Update collection metadata and optionally replace books.
    Requires: owner with collections:update_own OR collections:manage_any
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "collections:update", str(current_user.get("user_id") or "unknown")
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

    # Get collection
    result = await db.execute(
        select(Collection).where(
            Collection.id == collection_id, Collection.is_deleted == False
        )
    )
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Permission check
    if not check_collection_permission(collection, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this collection",
        )

    # Version check
    if collection.version != data.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version conflict. Expected {data.version}, got {collection.version}",
        )

    # Save old state
    await db.refresh(collection, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(collection)
    old_version = collection.version

    # Apply updates
    if data.name is not None:
        collection.name = data.name
    if data.description is not None:
        collection.description = data.description
    # Note: cover_key not settable via PATCH - use upload endpoints

    # Replace books if provided
    if data.book_ids is not None:
        await link_books_to_collection(
            db,
            collection,
            data.book_ids,
            clear_existing=True,
            error_when_book_not_found=False,  # waring when book not found
        )

    collection.version += 1
    collection.last_edited_by = current_user["user_id"]
    collection.last_edited_at = datetime.now(timezone.utc)

    # Record update
    await db.refresh(collection, ["books"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(collection),
        new_version=collection.version,
        old_version=old_version,
    )

    await db.commit()
    await db.refresh(collection)

    # Invalidate caches
    await cache.invalidate_collection(collection.id, r)

    return CollectionRead.model_validate(collection)


@router.post("/{collection_id}/rollback", response_model=CollectionRead)
async def rollback_collection_version(
    collection_id: int,
    data: CollectionRollbackRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Rollback collection to a previous version from edit history.

    Permission required:
    - Owner with 'collections:update_own' scope, OR
    - User with 'collections:edit_public_meta' scope (wiki editor)

    Creates a new version with the old data (does not revert version number).
    """
    rl_key = cache.make_rate_limit_key(
        "collections:rollback", str(current_user.get("user_id") or "unknown")
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

    # Fetch collection with books
    query = (
        select(Collection)
        .where(Collection.id == collection_id)
        .options(selectinload(Collection.books))
    )
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Check version conflict
    if collection.version != data.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version conflict. Current version is {collection.version}, you have {data.version}",
        )

    # Permission check
    is_owner = collection.created_by_user_id == current_user["user_id"]
    has_update_own = "collections:update_own" in current_user.get("scopes", [])
    has_edit_public_meta = "collections:edit_public_meta" in current_user.get(
        "scopes", []
    )

    if is_owner and has_update_own:
        pass
    elif has_edit_public_meta and collection.status == ContentStatus.APPROVED:
        pass
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to rollback this collection",
        )

    # Fetch target version from edit history
    history_query = (
        select(EditHistory)
        .where(
            and_(
                EditHistory.entity_type == "collection",
                EditHistory.entity_id == collection_id,
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

    # Capture old state BEFORE rollback for audit
    await db.refresh(collection, ["books"])  # Load relationship for serialization
    old_data_for_audit = serialize_entity(collection)
    old_version = collection.version

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
            collection.name = old_data["name"]
        if "description" in old_data:
            collection.description = old_data["description"]
        if "cover_key" in old_data:
            collection.cover_key = old_data["cover_key"]

        # Restore book associations if present using helper function
        if "book_ids" in old_data:
            book_ids = old_data["book_ids"]
            # Clear existing and restore historical book associations
            # Note: This will only restore books that are still APPROVED and public
            await link_books_to_collection(
                db,
                collection,
                book_ids,
                clear_existing=True,
            )

    # Increment version (rollback creates new version)
    collection.version += 1
    collection.last_edited_by = current_user["user_id"]
    collection.last_edited_at = datetime.now(timezone.utc)

    # Record rollback in history with correct pre/post snapshots
    await db.refresh(collection, ["books"])  # Refresh after changes for new_data
    await record_update(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        old_data=old_data_for_audit,
        new_data=serialize_entity(collection),
        new_version=collection.version,
        old_version=old_version,
    )

    await db.commit()

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)

    await db.refresh(collection)

    return CollectionRead.model_validate(collection)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Soft delete a collection.
    Requires: owner with collections:delete_own OR collections:manage_any
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "collections:delete", str(current_user.get("user_id") or "unknown")
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

    # Get collection
    result = await db.execute(
        select(Collection).where(
            Collection.id == collection_id, Collection.is_deleted == False
        )
    )
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Permission check
    if not check_delete_permission(collection, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this collection",
        )

    # Save old state
    await db.refresh(collection, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(collection)

    # Soft delete
    collection.is_deleted = True
    collection.deleted_at = datetime.now(timezone.utc)

    # Record delete
    await record_delete(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        data=old_data,
        version=collection.version,
    )

    await db.commit()

    # Invalidate caches
    await cache.invalidate_collection(collection.id, r)


# ========================================
# BOOK MANAGEMENT ENDPOINTS
# ========================================


@router.post("/{collection_id}/books", response_model=CollectionBookAddResponse)
async def add_book_to_collection(
    collection_id: int,
    data: CollectionBookAdd,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Add a book to collection at specified position.
    Position is clamped to valid range [1, current_max + 1].
    """
    # Get collection
    result = await db.execute(
        select(Collection)
        .where(Collection.id == collection_id, Collection.is_deleted == False)
        .options(selectinload(Collection.books))
    )
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Permission check
    if not check_collection_permission(collection, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this collection",
        )

    # Max 100 books check
    if collection.book_count >= 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Collection has reached maximum of 100 books",
        )

    # Validate book
    book = await db.scalar(
        select(Book).where(
            and_(
                Book.id == data.book_id,
                Book.status == ContentStatus.APPROVED,
                Book.is_public == True,
                Book.is_deleted == False,
            )
        )
    )
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found or not public",
        )

    # Check if already in collection
    existing = await db.scalar(
        select(CollectionBook).where(
            and_(
                CollectionBook.collection_id == collection_id,
                CollectionBook.book_id == data.book_id,
            )
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Book already in collection",
        )

    # Clamp position
    current_max = collection.book_count
    position = max(1, min(data.position, current_max + 1))

    # Shift existing books at >= position down
    await db.execute(
        text(
            """
            UPDATE collection_books
            SET position = position + 1
            WHERE collection_id = :cid AND position >= :pos
            """
        ),
        {"cid": collection_id, "pos": position},
    )

    # Add book
    cb = CollectionBook(
        collection_id=collection_id,
        book_id=data.book_id,
        position=position,
        added_at=datetime.now(timezone.utc),
    )
    db.add(cb)

    collection.book_count += 1
    collection.version += 1

    # Record update
    await record_update(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        old_data={"action": "add_book", "book_id": data.book_id},
        new_data={"action": "add_book", "book_id": data.book_id, "position": position},
        new_version=collection.version,
        old_version=collection.version - 1,
    )

    await db.commit()

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)

    return CollectionBookAddResponse(
        message="Book added",
        position=position,
        book_count=collection.book_count,
    )


@router.patch(
    "/{collection_id}/books/{book_id}", response_model=CollectionBookReorderResponse
)
async def reorder_book_in_collection(
    collection_id: int,
    book_id: int,
    data: CollectionBookUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Reorder a book within the collection.
    """
    # Get collection
    result = await db.execute(
        select(Collection).where(
            Collection.id == collection_id, Collection.is_deleted == False
        )
    )
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Permission check
    if not check_collection_permission(collection, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this collection",
        )

    # Get book in collection
    cb = await db.scalar(
        select(CollectionBook).where(
            and_(
                CollectionBook.collection_id == collection_id,
                CollectionBook.book_id == book_id,
            )
        )
    )
    if not cb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not in collection",
        )

    old_position = cb.position
    new_position = max(1, min(data.position, collection.book_count))

    if old_position == new_position:
        return {"message": "No change", "position": old_position}

    # Shift positions
    if new_position < old_position:
        # Moving up - shift items in range down
        await db.execute(
            text(
                """
                UPDATE collection_books
                SET position = position + 1
                WHERE collection_id = :cid
                AND position >= :new_pos AND position < :old_pos
                """
            ),
            {"cid": collection_id, "new_pos": new_position, "old_pos": old_position},
        )
    else:
        # Moving down - shift items in range up
        await db.execute(
            text(
                """
                UPDATE collection_books
                SET position = position - 1
                WHERE collection_id = :cid
                AND position > :old_pos AND position <= :new_pos
                """
            ),
            {"cid": collection_id, "old_pos": old_position, "new_pos": new_position},
        )

    cb.position = new_position
    collection.version += 1

    await db.commit()

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)

    return CollectionBookReorderResponse(
        message="Book reordered",
        old_position=old_position,
        new_position=new_position,
    )


@router.delete(
    "/{collection_id}/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_book_from_collection(
    collection_id: int,
    book_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Remove a book from collection.
    """
    # Get collection
    result = await db.execute(
        select(Collection).where(
            Collection.id == collection_id, Collection.is_deleted == False
        )
    )
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Permission check
    if not check_collection_permission(collection, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this collection",
        )

    # Get book in collection
    cb = await db.scalar(
        select(CollectionBook).where(
            and_(
                CollectionBook.collection_id == collection_id,
                CollectionBook.book_id == book_id,
            )
        )
    )
    if not cb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not in collection",
        )

    removed_position = cb.position

    # Delete
    await db.delete(cb)

    # Shift remaining books up
    await db.execute(
        text(
            """
            UPDATE collection_books
            SET position = position - 1
            WHERE collection_id = :cid AND position > :pos
            """
        ),
        {"cid": collection_id, "pos": removed_position},
    )

    collection.book_count -= 1
    collection.version += 1

    # Record update
    await record_update(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        old_data={
            "action": "remove_book",
            "book_id": book_id,
            "position": removed_position,
        },
        new_data={"action": "remove_book", "book_id": book_id},
        new_version=collection.version,
        old_version=collection.version - 1,
    )

    await db.commit()

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)


# ========================================
# CURATOR ENDPOINTS
# ========================================


@router.post("/{collection_id}/approve", response_model=CollectionDetail)
async def approve_collection(
    collection_id: int,
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Curator instant approval (+10 trust to submitter).
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "collections:approve", str(current_user.get("user_id") or "unknown")
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

    # Get collection
    query = (
        select(Collection)
        .where(Collection.id == collection_id)
        .options(selectinload(Collection.books).selectinload(CollectionBook.book))
    )
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    if collection.status == ContentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Collection is already approved",
        )

    # Save old state
    await db.refresh(collection, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(collection)
    old_version = collection.version

    # Approve
    collection.status = ContentStatus.APPROVED
    collection.is_public = True
    collection.version += 1

    # Record approval
    await db.refresh(collection, ["books"])  # Refresh after changes for new_data
    await record_approval(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(collection),
        new_version=collection.version,
        old_version=old_version,
    )

    # Clear jury votes
    await clear_jury_votes(db, "collection", collection.id)

    await db.commit()
    await db.refresh(collection)

    # Award trust (+10)
    try:
        await adjust_trust_for_approval(
            user_id=collection.created_by_user_id,
            entity_type="collection",
            entity_id=collection.id,
        )
    except Exception as e:
        logger.warning("Failed to adjust trust for collection approval: %s", e)

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)

    # Re-query with relationships for response
    query = (
        select(Collection)
        .where(Collection.id == collection_id)
        .options(selectinload(Collection.books).selectinload(CollectionBook.book))
    )
    result = await db.execute(query)
    collection = result.scalar_one()

    return CollectionDetail.model_validate(collection)


@router.post("/{collection_id}/reject", response_model=CollectionDetail)
async def reject_collection(
    collection_id: int,
    reason: str = Query(..., description="Rejection reason"),
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Curator rejection (-5 trust to submitter).
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "collections:reject", str(current_user.get("user_id") or "unknown")
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

    # Get collection
    query = (
        select(Collection)
        .where(Collection.id == collection_id)
        .options(selectinload(Collection.books).selectinload(CollectionBook.book))
    )
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    if collection.status == ContentStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Collection is already rejected",
        )

    # Save old state
    await db.refresh(collection, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(collection)
    old_version = collection.version

    # Reject
    collection.status = ContentStatus.REJECTED
    collection.is_public = False
    collection.version += 1

    # Record rejection
    await db.refresh(collection, ["books"])  # Refresh after changes for new_data
    await record_rejection(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(collection),
        new_version=collection.version,
        old_version=old_version,
    )

    # Clear jury votes
    await clear_jury_votes(db, "collection", collection.id)

    await db.commit()
    await db.refresh(collection)

    # Deduct trust (-5)
    try:
        await adjust_trust_for_rejection(
            user_id=collection.created_by_user_id,
            entity_type="collection",
            entity_id=collection.id,
        )
    except Exception as e:
        logger.warning("Failed to adjust trust for collection rejection: %s", e)

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)

    # Re-query with relationships for response
    query = (
        select(Collection)
        .where(Collection.id == collection_id)
        .options(selectinload(Collection.books).selectinload(CollectionBook.book))
    )
    result = await db.execute(query)
    collection = result.scalar_one()

    return CollectionDetail.model_validate(collection)


@router.post("/{collection_id}/recover", response_model=CollectionDetail)
async def recover_collection(
    collection_id: int,
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Recover a soft-deleted collection.
    """
    # Rate limiting
    rl_key = cache.make_rate_limit_key(
        "collections:recover", str(current_user.get("user_id") or "unknown")
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

    # Get deleted collection
    query = (
        select(Collection)
        .where(Collection.id == collection_id, Collection.is_deleted == True)
        .options(selectinload(Collection.books).selectinload(CollectionBook.book))
    )
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deleted collection not found",
        )

    # Save old state
    await db.refresh(collection, ["books"])  # Load relationship for serialization
    old_data = serialize_entity(collection)
    old_version = collection.version

    # Recover
    collection.is_deleted = False
    collection.deleted_at = None
    collection.version += 1

    # Record recovery
    await db.refresh(collection, ["books"])  # Refresh after changes for new_data
    await record_recovery(
        db=db,
        entity_type="collection",
        entity_id=collection.id,
        user_id=current_user["user_id"],
        old_data=old_data,
        new_data=serialize_entity(collection),
        new_version=collection.version,
        old_version=old_version,
    )

    await db.commit()
    await db.refresh(collection)

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)

    # Re-query with relationships for response
    query = (
        select(Collection)
        .where(Collection.id == collection_id)
        .options(selectinload(Collection.books).selectinload(CollectionBook.book))
    )
    result = await db.execute(query)
    collection = result.scalar_one()

    return CollectionDetail.model_validate(collection)


# ========================================
# SOCIAL ENDPOINTS
# ========================================


@router.post("/{collection_id}/subscribe", response_model=SubscriptionResponse)
async def subscribe_to_collection(
    collection_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Subscribe to collection updates.
    NO TRUST REWARDS - prevents farming.
    """
    # Get collection
    result = await db.execute(
        select(Collection).where(
            and_(
                Collection.id == collection_id,
                Collection.status == ContentStatus.APPROVED,
                Collection.is_public == True,
                Collection.is_deleted == False,
            )
        )
    )
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found"
        )

    # Check existing subscription
    existing = await db.scalar(
        select(CollectionSubscription).where(
            and_(
                CollectionSubscription.collection_id == collection_id,
                CollectionSubscription.user_id == current_user["user_id"],
            )
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already subscribed",
        )

    # Create subscription
    subscription = CollectionSubscription(
        collection_id=collection_id,
        user_id=current_user["user_id"],
    )
    db.add(subscription)

    collection.subscriber_count += 1

    await db.commit()

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)

    return SubscriptionResponse(
        message="Subscribed",
        subscriber_count=collection.subscriber_count,
    )


@router.delete("/{collection_id}/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_from_collection(
    collection_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(cache.get_redis),
):
    """
    Unsubscribe from collection updates.
    """
    # Get subscription
    subscription = await db.scalar(
        select(CollectionSubscription).where(
            and_(
                CollectionSubscription.collection_id == collection_id,
                CollectionSubscription.user_id == current_user["user_id"],
            )
        )
    )

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not subscribed",
        )

    # Get collection for count update
    collection = await db.scalar(
        select(Collection).where(Collection.id == collection_id)
    )
    if collection:
        collection.subscriber_count = max(0, collection.subscriber_count - 1)

    await db.delete(subscription)
    await db.commit()

    # Invalidate cache
    await cache.invalidate_collection(collection_id, r)
