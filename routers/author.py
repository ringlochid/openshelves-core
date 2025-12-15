"""
Author router with wiki-style workflow and RBAC.
Implements Phase 2 author management endpoints.
"""
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_async_db
from dependencies.auth import get_current_user, require_scope, require_role, require_min_trust
from models import Author, Book, AuthorFollow, ContentStatus
from schemas.author import (
    AuthorCreate,
    AuthorUpdate,
    AuthorDetail,
    AuthorRead,
    AuthorListResponse,
)
from helpers.edit_history import (
    check_version_conflict,
    record_create,
    record_update,
    record_delete,
    record_approval,
    record_rejection,
    serialize_entity,
)
from services.auth_client import adjust_trust_for_approval, adjust_trust_for_rejection, adjust_trust_for_social_bonus


router = APIRouter(prefix="/authors", tags=["Authors"])


# ========================================
# PUBLIC ENDPOINTS (No Auth Required)
# ========================================

@router.get("", response_model=AuthorListResponse)
async def list_authors(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search by name"),
    sort: str = Query("name", regex="^(name|follower_count|created_at)$"),
    order: str = Query("asc", regex="^(asc|desc)$"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all approved, public authors.
    No authentication required.
    """
    # Base query - only show approved, public, non-deleted authors
    query = select(Author).where(
        and_(
            Author.status == ContentStatus.APPROVED,
            Author.is_public == True,
            Author.is_deleted == False,
        )
    )
    
    # Search filter
    if search:
        query = query.where(Author.name.ilike(f"%{search}%"))
    
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
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get detailed author information.
    Only shows approved, public authors to unauthenticated users.
    """
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
        .options(selectinload(Author.books))
    )
    
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    return AuthorDetail.model_validate(author)


@router.get("/{author_id}/books", response_model=list[dict])
async def get_author_books(
    author_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get all approved books by an author.
    Only shows books from approved, public authors.
    """
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    # Get approved books by this author
    books_query = (
        select(Book)
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
    
    return [
        {
            "id": book.id,
            "title": book.title,
            "isbn": book.isbn,
            "publication_year": book.publication_year,
            "description": book.description,
            "cover_image_key": book.cover_image_key,
        }
        for book in books
    ]


# ========================================
# AUTHENTICATED ENDPOINTS
# ========================================

@router.post("", response_model=AuthorDetail, status_code=status.HTTP_201_CREATED)
async def create_author(
    data: AuthorCreate,
    current_user: dict = Depends(require_scope("authors:draft")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Submit a new author for approval.
    Requires 'authors:draft' scope (available to all users).
    """
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
                detail="One or more book IDs are invalid or not approved"
            )
    
    # Create author with PENDING status
    author = Author(
        name=data.name,
        email=data.email,
        bio=data.bio,
        avatar_key=data.avatar_key,
        created_by_user_id=current_user["user_id"],
        linked_user_id=data.linked_user_id,
        status=ContentStatus.PENDING,
        is_public=False,
        version=1,
    )
    
    if books:
        author.books = list(books)
    
    db.add(author)
    await db.flush()  # Get author.id before recording history
    
    # Record creation in edit history
    await record_create(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        data=serialize_entity(author),
    )
    
    await db.commit()
    await db.refresh(author)
    
    return AuthorDetail.model_validate(author)


@router.patch("/{author_id}", response_model=AuthorDetail)
async def update_author(
    author_id: int,
    data: AuthorUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update an author submission.
    Owner can edit if status is PENDING.
    Admins with content:edit_any can edit any author.
    """
    # Fetch author with books
    query = select(Author).where(Author.id == author_id).options(selectinload(Author.books))
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    # Check version for optimistic locking
    check_version_conflict(author.version, data.version, "author", author_id)
    
    # Check permissions
    is_owner = author.created_by_user_id == current_user["user_id"]
    has_edit_any = "authors:edit_public_meta" in current_user.get("scopes", [])
    
    if not (is_owner or has_edit_any):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this author"
        )
    
    # Owners can only edit PENDING submissions
    if is_owner and not has_edit_any and author.status != ContentStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit authors with PENDING status"
        )
    
    # Store old data for history
    old_data = author
    
    # Update fields
    if data.name is not None:
        author.name = data.name
    if data.email is not None:
        author.email = data.email
    if data.bio is not None:
        author.bio = data.bio
    if data.avatar_key is not None:
        author.avatar_key = data.avatar_key
    if data.linked_user_id is not None:
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
        books = result.scalars().all()
        
        if len(books) != len(data.book_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more book IDs are invalid or not approved"
            )
        
        author.books = list(books)
    
    # Increment version and update metadata
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)
    
    # Record update in edit history
    await record_update(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=serialize_entity(old_data),
        new_data=serialize_entity(author),
        new_version=author.version,
        old_version=author.version - 1,
    )
    
    await db.commit()
    await db.refresh(author)
    
    return AuthorDetail.model_validate(author)


@router.delete("/{author_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_author(
    author_id: int,
    current_user: dict = Depends(require_scope("content:takedown")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Soft-delete an author (curator/admin only).
    Sets is_deleted=True, deleted_at=now.
    Requires 'content:takedown' scope (curator role: trust_score >= 80, reputation >= 90%).
    """
    # Fetch author
    query = select(Author).where(Author.id == author_id)
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    if author.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Author is already deleted"
        )
    
    # Soft delete
    old_data = author
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
        data=serialize_entity(old_data),
        version=author.version - 1,
    )
    
    await db.commit()


@router.post("/{author_id}/follow", status_code=status.HTTP_201_CREATED)
async def follow_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Follow an author to receive notifications.
    Awards +3 trust points to author creator (max +6 per author).
    Requires authentication.
    """
    # Verify author exists and is approved
    query = select(Author).where(
        and_(
            Author.id == author_id,
            Author.status == ContentStatus.APPROVED,
            Author.is_public == True,
            Author.is_deleted == False,
        )
    )
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found or not approved"
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
            detail="Already following this author"
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
    
    # Award trust bonus (+3, max +6 enforced by Auth Service)
    try:
        await adjust_trust_for_social_bonus(
            user_id=author.created_by_user_id,
            action="follow",
            entity_type="author",
            entity_id=author.id,
        )
    except Exception as e:
        # Log but don't fail the follow
        print(f"Warning: Failed to adjust trust score: {e}")
    
    return {"message": "Successfully followed author"}


@router.delete("/{author_id}/follow", status_code=status.HTTP_204_NO_CONTENT)
async def unfollow_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Unfollow an author.
    Decrements follower count.
    Requires authentication.
    """
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
            detail="You are not following this author"
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


# ========================================
# ADMIN ENDPOINTS
# ========================================

@router.post("/{author_id}/approve", response_model=AuthorDetail)
async def approve_author(
    author_id: int,
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Approve an author submission.
    Requires 'jury:override' scope (curator role: trust_score >= 80, reputation >= 90%).
    Awards +10 trust points to submitter.
    """
    # Fetch author
    query = select(Author).where(Author.id == author_id)
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    if author.status == ContentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Author is already approved"
        )
    
    # Update status
    old_data = author
    author.status = ContentStatus.APPROVED
    author.is_public = True
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)
    
    # Record approval in edit history
    await record_approval(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=serialize_entity(old_data),
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
        print(f"Warning: Failed to adjust trust score: {e}")
    
    await db.refresh(author)
    return AuthorDetail.model_validate(author)


@router.post("/{author_id}/reject", response_model=AuthorDetail)
async def reject_author(
    author_id: int,
    reason: str = Query(..., description="Rejection reason"),
    current_user: dict = Depends(require_scope("jury:override")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reject an author submission.
    Requires 'jury:override' scope (curator role: trust_score >= 80, reputation >= 90%).
    Deducts -5 trust points from submitter.
    """
    # Fetch author
    query = select(Author).where(Author.id == author_id)
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    
    if not author:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Author not found"
        )
    
    if author.status == ContentStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Author is already rejected"
        )
    
    # Update status
    old_data = author
    author.status = ContentStatus.REJECTED
    author.is_public = False
    author.version += 1
    author.last_edited_by = current_user["user_id"]
    author.last_edited_at = datetime.now(timezone.utc)
    
    # Record rejection in edit history
    await record_rejection(
        db=db,
        entity_type="author",
        entity_id=author.id,
        user_id=current_user["user_id"],
        old_data=serialize_entity(old_data),
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
        print(f"Warning: Failed to adjust trust score: {e}")
    
    await db.refresh(author)
    return AuthorDetail.model_validate(author)
