"""
Tests for edge cases in jury voting system.
Covers boundary conditions and unusual scenarios.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_voting_after_curator_approved_fails(async_client: AsyncClient, test_db: AsyncSession):
    """Test that jury cannot vote on curator-approved author."""
    submitter_id = uuid4()
    
    # Create author that was curator-approved (status=APPROVED, vote_score could be 0)
    author = Author(
        name="Curator Approved",
        email="curator@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        vote_score=0,  # Curator approved without votes
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create contributor JWT
    contributor_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:vote"],
        trust_score=15,
    )
    
    # Try to vote (should fail)
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    assert response.status_code == 400
    assert "PENDING" in response.json()["detail"] or "already approved" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_curator_override_after_jury_approved(async_client: AsyncClient, test_db: AsyncSession):
    """Test that curator can still approve author that was already jury-approved."""
    submitter_id = uuid4()
    
    # Create author that was jury-approved
    author = Author(
        name="Jury Approved",
        email="jury@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        vote_score=5,  # Auto-published by jury
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create curator JWT
    curator_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=curator_id,
        scopes=["jury:override"],
        trust_score=85,
    )
    
    # Curator approves again (should succeed, idempotent)
    response = await async_client.post(
        f"/authors/{author.id}/approve",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    # Should either succeed (200) or indicate already approved (200/400)
    assert response.status_code in [200, 400]


@pytest.mark.asyncio
async def test_takedown_vs_delete_own_distinction(async_client: AsyncClient, test_db: AsyncSession):
    """Test that curator takedown and owner delete are distinct operations."""
    owner_id = uuid4()
    curator_id = uuid4()
    
    # Create two identical authors
    author1 = Author(
        name="Author 1",
        email="author1@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    author2 = Author(
        name="Author 2",
        email="author2@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add_all([author1, author2])
    await test_db.flush()
    await test_db.refresh(author1)
    await test_db.refresh(author2)
    
    # Owner deletes via DELETE /authors/{id} (authors:delete_own)
    owner_jwt = create_test_jwt(
        user_id=owner_id,
        scopes=["authors:delete_own"],
        trust_score=15,
    )
    
    response1 = await async_client.delete(
        f"/authors/{author1.id}",
        headers={"Authorization": f"Bearer {owner_jwt}"}
    )
    assert response1.status_code == 204
    
    # Curator takedown via DELETE /authors/{id}/admin (content:takedown)
    curator_jwt = create_test_jwt(
        user_id=curator_id,
        scopes=["content:takedown"],
        trust_score=85,
    )
    
    response2 = await async_client.delete(
        f"/authors/{author2.id}/admin",
        headers={"Authorization": f"Bearer {curator_jwt}"}
    )
    assert response2.status_code == 204
    
    # Verify both are soft-deleted
    await test_db.refresh(author1)
    await test_db.refresh(author2)
    assert author1.is_deleted is True
    assert author2.is_deleted is True


@pytest.mark.asyncio
async def test_wiki_editing_pending_author_fails(async_client: AsyncClient, test_db: AsyncSession):
    """Test that wiki editors cannot edit PENDING authors."""
    owner_id = uuid4()
    wiki_editor_id = uuid4()
    
    # Create PENDING author
    author = Author(
        name="Pending Author",
        email="pending@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create wiki editor JWT (has authors:edit_public_meta)
    jwt_token = create_test_jwt(
        user_id=wiki_editor_id,
        scopes=["authors:edit_public_meta"],
        trust_score=25,
    )
    
    # Try to edit (should fail because not APPROVED)
    response = await async_client.patch(
        f"/authors/{author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"bio": "Trying to wiki-edit pending", "version": author.version}
    )
    
    assert response.status_code == 403
    assert "Insufficient permissions" in response.json()["detail"] or "APPROVED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_follow_pending_author_fails(async_client: AsyncClient, test_db: AsyncSession):
    """Test that users cannot follow PENDING authors."""
    owner_id = uuid4()
    follower_id = uuid4()
    
    # Create PENDING author
    author = Author(
        name="Pending Author",
        email="pending@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create user JWT
    jwt_token = create_test_jwt(
        user_id=follower_id,
        scopes=["authors:follow"],
        trust_score=10,
    )
    
    # Try to follow (should fail)
    response = await async_client.post(
        f"/authors/{author.id}/follow",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    # Can be 400 (bad request) or 404 (not found - pending authors filtered out)
    assert response.status_code in [400, 404]
    if response.status_code == 400:
        assert "APPROVED" in response.json()["detail"] or "public" in response.json()["detail"].lower()
