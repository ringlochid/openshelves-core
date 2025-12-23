"""
Tests for owner inventory endpoints (/me).
Tests the endpoints where users can view all content they created.
"""

import pytest
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from models import Author, Book, Collection, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_get_my_authors_returns_all_statuses(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that /authors/me returns all statuses (PENDING, APPROVED, REJECTED)."""
    user_id = uuid4()

    # Create authors with different statuses
    pending_author = Author(
        name="Pending Author",
        email="pending@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    approved_author = Author(
        name="Approved Author",
        email="approved@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    rejected_author = Author(
        name="Rejected Author",
        email="rejected@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.REJECTED,
        is_public=False,
    )

    test_db.add_all([pending_author, approved_author, rejected_author])
    await test_db.flush()

    # Create JWT
    jwt_token = create_test_jwt(
        user_id=user_id, scopes=["authors:read"], trust_score=10
    )

    response = await async_client.get(
        "/authors/me", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    # Should return all 3 authors
    assert data["total"] >= 3
    author_names = [a["name"] for a in data["items"]]
    assert "Pending Author" in author_names
    assert "Approved Author" in author_names
    assert "Rejected Author" in author_names


@pytest.mark.asyncio
async def test_get_my_authors_excludes_deleted(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that /authors/me excludes soft-deleted authors."""
    user_id = uuid4()

    # Create active and deleted authors
    active_author = Author(
        name="Active Author",
        email="active@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        is_deleted=False,
    )
    deleted_author = Author(
        name="Deleted Author",
        email="deleted@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        is_deleted=True,
    )

    test_db.add_all([active_author, deleted_author])
    await test_db.flush()

    jwt_token = create_test_jwt(
        user_id=user_id, scopes=["authors:read"], trust_score=10
    )

    response = await async_client.get(
        "/authors/me", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    author_names = [a["name"] for a in data["items"]]
    assert "Active Author" in author_names
    assert "Deleted Author" not in author_names


@pytest.mark.asyncio
async def test_get_my_authors_only_shows_own(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that /authors/me only shows authors created by the current user."""
    user1_id = uuid4()
    user2_id = uuid4()

    # Create authors from different users
    user1_author = Author(
        name="User1 Author",
        email="user1@example.com",
        created_by_user_id=user1_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    user2_author = Author(
        name="User2 Author",
        email="user2@example.com",
        created_by_user_id=user2_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )

    test_db.add_all([user1_author, user2_author])
    await test_db.flush()

    # User1 should only see their own author
    jwt_token = create_test_jwt(
        user_id=user1_id, scopes=["authors:read"], trust_score=10
    )

    response = await async_client.get(
        "/authors/me", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    author_names = [a["name"] for a in data["items"]]
    assert "User1 Author" in author_names
    assert "User2 Author" not in author_names


@pytest.mark.asyncio
async def test_get_my_authors_pagination(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test pagination for /authors/me."""
    user_id = uuid4()

    # Create 5 authors
    for i in range(5):
        author = Author(
            name=f"Author {i}",
            email=f"author{i}@example.com",
            created_by_user_id=user_id,
            status=ContentStatus.APPROVED,
            is_public=True,
        )
        test_db.add(author)
    await test_db.flush()

    jwt_token = create_test_jwt(
        user_id=user_id, scopes=["authors:read"], trust_score=10
    )

    # Request page 1 with 2 items per page
    response = await async_client.get(
        "/authors/me?page=1&per_page=2",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["page"] == 1
    assert data["per_page"] == 2
    assert len(data["items"]) == 2
    assert data["total"] >= 5
    assert data["pages"] >= 3


@pytest.mark.asyncio
async def test_get_my_books_returns_all_statuses(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that /books/me returns all statuses."""
    user_id = uuid4()

    # Create books with different statuses
    pending_book = Book(
        title="Pending Book",
        created_by_user_id=user_id,
        status=ContentStatus.PENDING,
        is_public=False,
        tags=[],
    )
    approved_book = Book(
        title="Approved Book",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        tags=[],
    )

    test_db.add_all([pending_book, approved_book])
    await test_db.flush()

    jwt_token = create_test_jwt(user_id=user_id, scopes=["books:read"], trust_score=10)

    response = await async_client.get(
        "/books/me", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    book_titles = [b["title"] for b in data["items"]]
    assert "Pending Book" in book_titles
    assert "Approved Book" in book_titles


@pytest.mark.asyncio
async def test_get_my_collections_returns_all_statuses(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that /collections/me returns all statuses."""
    user_id = uuid4()

    # Create collections with different statuses
    pending_collection = Collection(
        name="Pending Collection",
        created_by_user_id=user_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    approved_collection = Collection(
        name="Approved Collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )

    test_db.add_all([pending_collection, approved_collection])
    await test_db.flush()

    jwt_token = create_test_jwt(
        user_id=user_id, scopes=["collections:read"], trust_score=10
    )

    response = await async_client.get(
        "/collections/me", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    collection_names = [c["name"] for c in data["items"]]
    assert "Pending Collection" in collection_names
    assert "Approved Collection" in collection_names


@pytest.mark.asyncio
async def test_get_my_authors_empty_returns_empty_list(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that /authors/me returns empty list when user has no authors."""
    user_id = uuid4()

    jwt_token = create_test_jwt(
        user_id=user_id, scopes=["authors:read"], trust_score=10
    )

    response = await async_client.get(
        "/authors/me", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_get_my_endpoints_require_auth(async_client: AsyncClient):
    """Test that /me endpoints require authentication."""
    # No auth header
    response = await async_client.get("/authors/me")
    assert response.status_code == 401

    response = await async_client.get("/books/me")
    assert response.status_code == 401

    response = await async_client.get("/collections/me")
    assert response.status_code == 401
