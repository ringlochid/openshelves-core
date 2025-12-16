"""
Tests for delete_own_book endpoint.

Verifies that users can delete their own books with proper permissions.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, Book, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_owner_can_delete_own_book(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """User with books:delete_own scope can delete their own book."""
    user_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=user_id,
        scopes=["books:draft", "books:delete_own", "books:publish_direct"],
        trust_score=60,
    )
    
    # Create author
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    
    # Create book
    book = Book(
        title="Test Book",
        year=2023,
        file_format="pdf",
        tags=[],
        status=ContentStatus.APPROVED,
        is_public=True,
        created_by_user_id=user_id,
    )
    book.authors.append(author)
    test_db.add(book)
    await test_db.commit()
    await test_db.refresh(book)
    book_id = book.id
    
    # Delete own book
    response = await async_client.delete(
        f"/books/{book_id}/own",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    
    assert response.status_code == 204
    
    # Verify book is soft-deleted
    await test_db.refresh(book)
    assert book.is_deleted is True
    assert book.is_public is False


@pytest.mark.asyncio
async def test_non_owner_cannot_delete_book(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """User cannot delete someone else's book even with delete_own scope."""
    owner_id = uuid4()
    other_user_id = uuid4()
    
    # Create author and book owned by owner_id
    author = Author(
        name="Owner Author",
        email="owner@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    
    book = Book(
        title="Owner's Book",
        year=2023,
        file_format="pdf",
        tags=[],
        status=ContentStatus.APPROVED,
        is_public=True,
        created_by_user_id=owner_id,
    )
    book.authors.append(author)
    test_db.add(book)
    await test_db.commit()
    await test_db.refresh(book)
    book_id = book.id
    
    # Other user tries to delete
    other_jwt = create_test_jwt(
        user_id=other_user_id,
        scopes=["books:draft", "books:delete_own"],
        trust_score=60,
    )
    
    response = await async_client.delete(
        f"/books/{book_id}/own",
        headers={"Authorization": f"Bearer {other_jwt}"},
    )
    
    assert response.status_code == 403
    assert "only delete your own books" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_own_without_scope_fails(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """User without books:delete_own scope cannot delete their own book."""
    user_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=user_id,
        scopes=["books:draft"],  # Missing books:delete_own
        trust_score=60,
    )
    
    # Create author and book
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    
    book = Book(
        title="Test Book",
        year=2023,
        file_format="pdf",
        tags=[],
        status=ContentStatus.APPROVED,
        is_public=True,
        created_by_user_id=user_id,
    )
    book.authors.append(author)
    test_db.add(book)
    await test_db.commit()
    await test_db.refresh(book)
    book_id = book.id
    
    # Try to delete without scope
    response = await async_client.delete(
        f"/books/{book_id}/own",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    
    assert response.status_code == 403
    assert "Missing required scopes" in response.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_delete_already_deleted_book(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """Cannot delete a book that's already deleted."""
    user_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=user_id,
        scopes=["books:draft", "books:delete_own", "books:publish_direct"],
        trust_score=60,
    )
    
    # Create author
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    
    # Create book
    book = Book(
        title="Test Book",
        year=2023,
        file_format="pdf",
        tags=[],
        status=ContentStatus.APPROVED,
        is_public=True,
        created_by_user_id=user_id,
    )
    book.authors.append(author)
    test_db.add(book)
    await test_db.commit()
    await test_db.refresh(book)
    book_id = book.id
    
    # Delete once
    response = await async_client.delete(
        f"/books/{book_id}/own",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert response.status_code == 204
    
    # Try to delete again
    response = await async_client.delete(
        f"/books/{book_id}/own",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    
    assert response.status_code == 400
    assert "already deleted" in response.json()["detail"]
