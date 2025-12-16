"""
Tests for curator recovery endpoint.
Tests recovery of soft-deleted authors within 24h window.
"""
import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy import select

from models import Author, ContentStatus, EditHistory, EditAction
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_curator_recovers_deleted_author_within_24h(
    async_client: AsyncClient,
    test_db,
):
    """Curator can recover deleted author within 24-hour window."""
    # Create author
    user_id = "11111111-1111-1111-1111-111111111111"
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        version=1,
    )
    test_db.add(author)
    await test_db.flush()
    author_id = author.id
    
    # Soft delete author (5 hours ago)
    author.is_deleted = True
    author.deleted_at = datetime.now(timezone.utc) - timedelta(hours=5)
    author.is_public = False
    author.version = 2
    await test_db.flush()
    
    # Curator recovers author
    curator_id = "22222222-2222-2222-2222-222222222222"
    token = create_test_jwt(user_id=curator_id, scopes=["jury:override"])
    
    response = await async_client.post(
        f"/authors/{author_id}/recover",
        headers={"Authorization": f"Bearer {token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Check response
    assert data["id"] == author_id
    assert data["name"] == "Test Author"
    assert data["status"] == "APPROVED"
    assert data["is_public"] is True  # Restored to public because APPROVED
    assert data["version"] == 3  # Incremented
    
    # Verify in database
    result = await test_db.execute(select(Author).where(Author.id == author_id))
    author = result.scalar_one()
    assert author.is_deleted is False
    assert author.deleted_at is None
    assert author.is_public is True
    assert author.version == 3


@pytest.mark.asyncio
async def test_recovery_fails_after_24h_window(
    async_client: AsyncClient,
    test_db,
):
    """Recovery fails if author was deleted more than 24 hours ago."""
    # Create deleted author (25 hours ago)
    user_id = "11111111-1111-1111-1111-111111111111"
    author = Author(
        name="Old Deleted Author",
        email="old@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=False,
        version=1,
    )
    test_db.add(author)
    await test_db.flush()
    author_id = author.id
    
    author.is_deleted = True
    author.deleted_at = datetime.now(timezone.utc) - timedelta(hours=25)
    await test_db.flush()
    
    # Attempt recovery
    curator_id = "22222222-2222-2222-2222-222222222222"
    token = create_test_jwt(user_id=curator_id, scopes=["jury:override"])
    
    response = await async_client.post(
        f"/authors/{author_id}/recover",
        headers={"Authorization": f"Bearer {token}"},
    )
    
    assert response.status_code == 410  # Gone
    assert "Recovery window expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_recovery_permission_requires_curator(
    async_client: AsyncClient,
    test_db,
):
    """Only curators with jury:override can recover deleted content."""
    # Create deleted author
    user_id = "11111111-1111-1111-1111-111111111111"
    author = Author(
        name="Deleted Author",
        email="deleted@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=False,
        version=1,
    )
    test_db.add(author)
    await test_db.flush()
    author_id = author.id
    
    author.is_deleted = True
    author.deleted_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await test_db.flush()
    
    # Regular user (no jury:override) attempts recovery
    regular_user_id = "33333333-3333-3333-3333-333333333333"
    token = create_test_jwt(user_id=regular_user_id, scopes=["authors:draft"])
    
    response = await async_client.post(
        f"/authors/{author_id}/recover",
        headers={"Authorization": f"Bearer {token}"},
    )
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_recovery_fails_for_non_deleted_author(
    async_client: AsyncClient,
    test_db,
):
    """Cannot recover an author that is not deleted."""
    # Create non-deleted author
    user_id = "11111111-1111-1111-1111-111111111111"
    author = Author(
        name="Active Author",
        email="active@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        version=1,
    )
    test_db.add(author)
    await test_db.flush()
    author_id = author.id
    
    # Attempt recovery
    curator_id = "22222222-2222-2222-2222-222222222222"
    token = create_test_jwt(user_id=curator_id, scopes=["jury:override"])
    
    response = await async_client.post(
        f"/authors/{author_id}/recover",
        headers={"Authorization": f"Bearer {token}"},
    )
    
    assert response.status_code == 400
    assert "not deleted" in response.json()["detail"]


@pytest.mark.asyncio
async def test_recovery_restores_public_based_on_status(
    async_client: AsyncClient,
    test_db,
):
    """Recovery sets is_public based on status (APPROVED=True, PENDING/REJECTED=False)."""
    # Test 1: PENDING author should not be public after recovery
    user_id = "11111111-1111-1111-1111-111111111111"
    pending_author = Author(
        name="Pending Author",
        email="pending@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.PENDING,
        is_public=False,
        version=1,
    )
    test_db.add(pending_author)
    await test_db.flush()
    
    pending_author.is_deleted = True
    pending_author.deleted_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await test_db.flush()
    
    curator_id = "22222222-2222-2222-2222-222222222222"
    token = create_test_jwt(user_id=curator_id, scopes=["jury:override"])
    
    response = await async_client.post(
        f"/authors/{pending_author.id}/recover",
        headers={"Authorization": f"Bearer {token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PENDING"
    assert data["is_public"] is False  # PENDING should not be public
    
    # Test 2: APPROVED author should be public after recovery
    approved_author = Author(
        name="Approved Author",
        email="approved@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=False,  # Was public before deletion
        version=1,
    )
    test_db.add(approved_author)
    await test_db.flush()
    
    approved_author.is_deleted = True
    approved_author.deleted_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await test_db.flush()
    
    response = await async_client.post(
        f"/authors/{approved_author.id}/recover",
        headers={"Authorization": f"Bearer {token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "APPROVED"
    assert data["is_public"] is True  # APPROVED should be public
