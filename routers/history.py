"""
Edit History router for Library Service.
Provides audit trail viewing with scope-based access control.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_async_db
from dependencies.auth import get_current_user_optional
from models import Author, Book, Collection, EditHistory
from schemas.history import (
    EditHistoryRead,
    EditHistorySummary,
    EditHistoryListResponse,
)

router = APIRouter(tags=["Edit History"])


# ========================================
# Permission Helper
# ========================================


def can_view_entity_history(entity, current_user: dict | None) -> bool:
    """
    Check if user can view edit history for an entity.

    Access rules (simplified for transparency):
    - Public entities: Anyone can view history
    - Owner: Always can view their own entity's history
    - Jury members: Can view ALL entity history (for abuse reporting)
    """
    # Public entities are transparent
    if entity.is_public:
        return True

    # No user info = deny for non-public
    if not current_user:
        return False

    # Owner can always view their own entity history
    user_id = current_user.get("user_id")
    if user_id and entity.created_by_user_id == user_id:
        return True

    # Jury members can view all history (transparency for abuse reporting)
    scopes = current_user.get("scopes", [])
    if "jury:view" in scopes:
        return True

    return False


def extract_change_counts(changes: dict | None) -> tuple[int, int, int]:
    """Extract added/modified/removed counts from changes dict."""
    if not changes:
        return 0, 0, 0

    added = len(changes.get("added", []))
    modified = len(changes.get("modified", []))
    removed = len(changes.get("removed", []))
    return added, modified, removed


# ========================================
# Book History Endpoints
# ========================================


@router.get(
    "/books/{book_id}/history",
    response_model=EditHistoryListResponse,
    summary="Get book edit history",
)
async def get_book_history(
    book_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: dict | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get paginated edit history for a book.

    Access: Public books viewable by anyone, owners and jury:view can see all.
    """
    # Fetch book to check permissions
    book = await db.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if not can_view_entity_history(book, current_user):
        raise HTTPException(
            status_code=403, detail="Not authorized to view this book's history"
        )

    # Query history with pagination
    base_query = select(EditHistory).where(
        EditHistory.entity_type == "book",
        EditHistory.entity_id == book_id,
    )

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total = await db.scalar(count_query) or 0

    # Fetch page
    offset = (page - 1) * per_page
    history_query = (
        base_query.order_by(desc(EditHistory.created_at)).offset(offset).limit(per_page)
    )
    result = await db.execute(history_query)
    records = result.scalars().all()

    # Convert to summary with counts
    items = []
    for record in records:
        added, modified, removed = extract_change_counts(record.changes)
        items.append(
            EditHistorySummary(
                id=record.id,
                action=record.action,
                user_id=record.user_id,
                version=record.version,
                created_at=record.created_at,
                added_count=added,
                modified_count=modified,
                removed_count=removed,
            )
        )

    pages = (total + per_page - 1) // per_page if per_page else 1

    return EditHistoryListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ========================================
# Author History Endpoints
# ========================================


@router.get(
    "/authors/{author_id}/history",
    response_model=EditHistoryListResponse,
    summary="Get author edit history",
)
async def get_author_history(
    author_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: dict | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get paginated edit history for an author.

    Access: Public authors viewable by anyone, owners and jury:view can see all.
    """
    author = await db.get(Author, author_id)
    if not author:
        raise HTTPException(status_code=404, detail="Author not found")

    if not can_view_entity_history(author, current_user):
        raise HTTPException(
            status_code=403, detail="Not authorized to view this author's history"
        )

    base_query = select(EditHistory).where(
        EditHistory.entity_type == "author",
        EditHistory.entity_id == author_id,
    )

    count_query = select(func.count()).select_from(base_query.subquery())
    total = await db.scalar(count_query) or 0

    offset = (page - 1) * per_page
    history_query = (
        base_query.order_by(desc(EditHistory.created_at)).offset(offset).limit(per_page)
    )
    result = await db.execute(history_query)
    records = result.scalars().all()

    items = []
    for record in records:
        added, modified, removed = extract_change_counts(record.changes)
        items.append(
            EditHistorySummary(
                id=record.id,
                action=record.action,
                user_id=record.user_id,
                version=record.version,
                created_at=record.created_at,
                added_count=added,
                modified_count=modified,
                removed_count=removed,
            )
        )

    pages = (total + per_page - 1) // per_page if per_page else 1

    return EditHistoryListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ========================================
# Collection History Endpoints
# ========================================


@router.get(
    "/collections/{collection_id}/history",
    response_model=EditHistoryListResponse,
    summary="Get collection edit history",
)
async def get_collection_history(
    collection_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: dict | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get paginated edit history for a collection.

    Access: Public collections viewable by anyone, owners and jury:view can see all.
    """
    collection = await db.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if not can_view_entity_history(collection, current_user):
        raise HTTPException(
            status_code=403, detail="Not authorized to view this collection's history"
        )

    base_query = select(EditHistory).where(
        EditHistory.entity_type == "collection",
        EditHistory.entity_id == collection_id,
    )

    count_query = select(func.count()).select_from(base_query.subquery())
    total = await db.scalar(count_query) or 0

    offset = (page - 1) * per_page
    history_query = (
        base_query.order_by(desc(EditHistory.created_at)).offset(offset).limit(per_page)
    )
    result = await db.execute(history_query)
    records = result.scalars().all()

    items = []
    for record in records:
        added, modified, removed = extract_change_counts(record.changes)
        items.append(
            EditHistorySummary(
                id=record.id,
                action=record.action,
                user_id=record.user_id,
                version=record.version,
                created_at=record.created_at,
                added_count=added,
                modified_count=modified,
                removed_count=removed,
            )
        )

    pages = (total + per_page - 1) // per_page if per_page else 1

    return EditHistoryListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ========================================
# History Detail Endpoint
# ========================================


@router.get(
    "/history/{history_id}",
    response_model=EditHistoryRead,
    summary="Get edit history detail",
)
async def get_history_detail(
    history_id: int,
    current_user: dict | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get full detail of a specific edit history record.

    Includes old_data, new_data, and changes for diff display.
    Access same as list endpoint (public/owner/jury:view).
    """
    record = await db.get(EditHistory, history_id)
    if not record:
        raise HTTPException(status_code=404, detail="History record not found")

    # Check permission based on entity type
    entity = None
    if record.entity_type == "book":
        entity = await db.get(Book, record.entity_id)
    elif record.entity_type == "author":
        entity = await db.get(Author, record.entity_id)
    elif record.entity_type == "collection":
        entity = await db.get(Collection, record.entity_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid entity type")

    # If entity deleted, only jury:view can see
    if not entity:
        scopes = (current_user or {}).get("scopes", [])
        if "jury:view" not in scopes:
            raise HTTPException(status_code=404, detail="Entity not found")
    else:
        if not can_view_entity_history(entity, current_user):
            raise HTTPException(
                status_code=403, detail="Not authorized to view this history"
            )

    return EditHistoryRead.model_validate(record)


# TODO: Add crossservice endpoint for user service to check if a history exists
