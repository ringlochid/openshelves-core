"""
Author router with wiki-style workflow and RBAC.
Implements Phase 2 author management endpoints.
"""
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
    total = await db.scalar(count_query)
    
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
    current_user: dict = Depends(require_scope("content:submit")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Submit a new author for approval.
    Requires 'content:submit' scope.
    """
    # TODO: Phase 2.2 - Implement author creation with workflow
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Author creation will be implemented in Phase 2.2"
    )


@router.patch("/{author_id}", response_model=AuthorDetail)
async def update_author(
    author_id: int,
    data: AuthorUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update an author submission.
    Owner can edit if status is PENDING or CHANGES_REQUESTED.
    Admin can edit any author.
    """
    # TODO: Phase 2.2 - Implement author updates with version checking
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Author updates will be implemented in Phase 2.2"
    )


@router.delete("/{author_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_author(
    author_id: int,
    current_user: dict = Depends(require_scope("content:delete_any")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Soft-delete an author (admin only).
    Requires 'content:delete_any' scope.
    """
    # TODO: Phase 2.2 - Implement soft delete
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Author deletion will be implemented in Phase 2.2"
    )


@router.post("/{author_id}/follow", status_code=status.HTTP_201_CREATED)
async def follow_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Follow an author to receive notifications.
    Requires authentication.
    """
    # TODO: Phase 2.3 - Implement follow system
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Follow system will be implemented in Phase 2.3"
    )


@router.delete("/{author_id}/follow", status_code=status.HTTP_204_NO_CONTENT)
async def unfollow_author(
    author_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Unfollow an author.
    Requires authentication.
    """
    # TODO: Phase 2.3 - Implement unfollow
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Follow system will be implemented in Phase 2.3"
    )


# ========================================
# ADMIN ENDPOINTS
# ========================================

@router.post("/{author_id}/approve", response_model=AuthorDetail)
async def approve_author(
    author_id: int,
    current_user: dict = Depends(require_role("admin", "moderator")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Approve an author submission.
    Requires admin or moderator role.
    Awards trust points to submitter.
    """
    # TODO: Phase 2.2 - Implement approval workflow with trust adjustments
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Author approval will be implemented in Phase 2.2"
    )


@router.post("/{author_id}/reject", response_model=AuthorDetail)
async def reject_author(
    author_id: int,
    reason: str = Query(..., description="Rejection reason"),
    current_user: dict = Depends(require_role("admin", "moderator")),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reject an author submission.
    Requires admin or moderator role.
    Deducts trust points from submitter.
    """
    # TODO: Phase 2.2 - Implement rejection workflow with trust penalties
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Author rejection will be implemented in Phase 2.2"
    )
