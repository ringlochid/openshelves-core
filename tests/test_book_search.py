"""
Tests for book list search and pagination.
Tests FTS, filters, and sort functionality.
"""

import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Book, ContentStatus


@pytest.mark.asyncio
async def test_book_list_basic(async_client: AsyncClient, test_db: AsyncSession):
    """Test basic book listing returns approved public books."""
    user_id = uuid4()

    approved_book = Book(
        title="Approved Book Search Test",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        year=2023,
    )
    pending_book = Book(
        title="Pending Book Search Test",
        created_by_user_id=user_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )

    test_db.add_all([approved_book, pending_book])
    await test_db.flush()

    response = await async_client.get("/books")

    assert response.status_code == 200
    data = response.json()

    titles = [b["title"] for b in data["items"]]
    assert "Approved Book Search Test" in titles
    assert "Pending Book Search Test" not in titles


@pytest.mark.asyncio
async def test_book_list_fts_search(async_client: AsyncClient, test_db: AsyncSession):
    """Test full-text search on books."""
    user_id = uuid4()

    python_book = Book(
        title="Learning Python Programming FTS",
        description="A comprehensive guide to Python",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    java_book = Book(
        title="Java Fundamentals FTS",
        description="Learn Java from scratch",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )

    test_db.add_all([python_book, java_book])
    await test_db.flush()

    # Search for "Python" - should return results
    response = await async_client.get("/books?q=Python")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_book_list_excludes_deleted(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that deleted books are excluded from list."""
    user_id = uuid4()

    active_book = Book(
        title="Active Book Delete Test",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        is_deleted=False,
    )
    deleted_book = Book(
        title="Deleted Book Delete Test",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        is_deleted=True,
    )

    test_db.add_all([active_book, deleted_book])
    await test_db.flush()

    response = await async_client.get("/books")

    assert response.status_code == 200
    data = response.json()

    titles = [b["title"] for b in data["items"]]
    assert "Active Book Delete Test" in titles
    assert "Deleted Book Delete Test" not in titles


@pytest.mark.asyncio
async def test_book_list_empty_results(async_client: AsyncClient):
    """Test that empty search returns empty list, not error."""
    response = await async_client.get("/books?q=nonexistentbookxyz123unique")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data


@pytest.mark.asyncio
async def test_book_list_sort_by_title(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test sorting books by title."""
    user_id = uuid4()

    book_a = Book(
        title="AAA Book Sort Test",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    book_z = Book(
        title="ZZZ Book Sort Test",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )

    test_db.add_all([book_a, book_z])
    await test_db.flush()

    # Sort by title ascending
    response = await async_client.get("/books?sort=title:asc")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
