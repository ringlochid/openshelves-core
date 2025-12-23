from fastapi import HTTPException, status
from models import Collection, CollectionBook, Book
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from models import ContentStatus


def check_collection_permission(
    collection: Collection, user: dict, require_manage_any: bool = False
) -> bool:
    """
    Check if user has permission to modify collection.
    Returns True if owner with collections:update_own OR collections:manage_any.
    """
    user_scopes = user.get("scopes", [])
    user_id = user.get("user_id")

    if "collections:manage_any" in user_scopes:
        return True

    if require_manage_any:
        return False

    # Owner check
    is_owner = str(collection.created_by_user_id) == user_id
    if is_owner and "collections:update_own" in user_scopes:
        return True

    return False


def check_delete_permission(collection: Collection, user: dict) -> bool:
    """Check if user has permission to delete collection."""
    user_scopes = user.get("scopes", [])
    user_id = user.get("user_id")

    if "collections:manage_any" in user_scopes:
        return True

    is_owner = str(collection.created_by_user_id) == user_id
    if is_owner and "collections:delete_own" in user_scopes:
        return True

    return False


async def link_books_to_collection(
    db: AsyncSession,
    collection: Collection,
    book_ids: list[int],
    clear_existing: bool = False,
) -> int:
    """
    Link books to collection with positions.
    Returns number of books linked.
    """
    if clear_existing:
        # Remove existing books
        await db.execute(
            CollectionBook.__table__.delete().where(
                CollectionBook.collection_id == collection.id
            )
        )

    linked_count = 0
    for idx, book_id in enumerate(book_ids[:100]):  # Enforce max 100
        # Validate book exists and is public/approved
        book = await db.scalar(
            select(Book).where(
                and_(
                    Book.id == book_id,
                    Book.status == ContentStatus.APPROVED,
                    Book.is_public == True,
                    Book.is_deleted == False,
                )
            )
        )
        if not book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="At least one book not found",
            )

        # Check if already in collection
        existing = await db.scalar(
            select(CollectionBook).where(
                and_(
                    CollectionBook.collection_id == collection.id,
                    CollectionBook.book_id == book_id,
                )
            )
        )
        if existing:
            continue

        # Add with position = index + 1
        cb = CollectionBook(
            collection_id=collection.id,
            book_id=book_id,
            position=idx + 1,
            added_at=datetime.now(timezone.utc),
        )
        db.add(cb)
        linked_count += 1

    collection.book_count = (
        linked_count if clear_existing else collection.book_count + linked_count
    )
    return linked_count
