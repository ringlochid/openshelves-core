"""
Tests for author ownership permissions matrix.
Covers owner vs non-owner permissions, wiki editing, and delete distinctions.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_owner_can_update_own_pending_author(async_client: AsyncClient, test_db: AsyncSession):
    """Test that owner can update their own PENDING author."""
    owner_id = uuid4()
    
    # Create pending author owned by user
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create owner JWT with authors:update_own scope
    jwt_token = create_test_jwt(
        user_id=owner_id,
        scopes=["authors:update_own"],
        trust_score=15,
    )
    
    # Update author
    response = await async_client.patch(
        f"/authors/{author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"bio": "Updated bio", "version": author.version}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["bio"] == "Updated bio"


@pytest.mark.asyncio
async def test_owner_can_update_own_approved_author(async_client: AsyncClient, test_db: AsyncSession):
    """Test that owner can update their own APPROVED author."""
    owner_id = uuid4()
    
    # Create approved author owned by user
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create owner JWT with authors:update_own scope
    jwt_token = create_test_jwt(
        user_id=owner_id,
        scopes=["authors:update_own"],
        trust_score=15,
    )
    
    # Update author
    response = await async_client.patch(
        f"/authors/{author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"bio": "Updated bio", "version": author.version}
    )
    
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_owner_can_delete_own_author(async_client: AsyncClient, test_db: AsyncSession):
    """Test that owner can delete their own author with authors:delete_own scope."""
    owner_id = uuid4()
    
    # Create author owned by user
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create owner JWT with authors:delete_own scope
    jwt_token = create_test_jwt(
        user_id=owner_id,
        scopes=["authors:delete_own"],
        trust_score=15,
    )
    
    # Delete author
    response = await async_client.delete(
        f"/authors/{author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    assert response.status_code == 204
    
    # Verify soft delete
    await test_db.refresh(author)
    assert author.is_deleted is True
    assert author.deleted_at is not None


@pytest.mark.asyncio
async def test_non_owner_cannot_update_without_scope(async_client: AsyncClient, test_db: AsyncSession):
    """Test that non-owner without wiki edit scope cannot update approved author."""
    owner_id = uuid4()
    other_user_id = uuid4()
    
    # Create approved author owned by someone else
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create JWT for different user (no authors:edit_public_meta scope)
    jwt_token = create_test_jwt(
        user_id=other_user_id,
        scopes=["books:read"],
        trust_score=10,
    )
    
    # Try to update (should fail)
    response = await async_client.patch(
        f"/authors/{author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"bio": "Updated bio", "version": author.version}
    )
    
    assert response.status_code == 403
    assert "Insufficient permissions" in response.json()["detail"]


@pytest.mark.asyncio
async def test_wiki_editor_can_edit_approved_only(async_client: AsyncClient, test_db: AsyncSession):
    """Test that wiki editors can only edit APPROVED authors."""
    owner_id = uuid4()
    wiki_editor_id = uuid4()
    
    # Create APPROVED author
    approved_author = Author(
        name="Approved Author",
        email="approved@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    
    # Create PENDING author
    pending_author = Author(
        name="Pending Author",
        email="pending@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    
    test_db.add_all([approved_author, pending_author])
    await test_db.flush()
    await test_db.refresh(approved_author)
    await test_db.refresh(pending_author)
    
    # Create wiki editor JWT (has authors:edit_public_meta but not owner)
    jwt_token = create_test_jwt(
        user_id=wiki_editor_id,
        scopes=["authors:edit_public_meta"],
        trust_score=25,
    )
    
    # Update APPROVED author (should succeed)
    response1 = await async_client.patch(
        f"/authors/{approved_author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"bio": "Wiki-edited bio", "version": approved_author.version}
    )
    assert response1.status_code == 200
    
    # Try to update PENDING author (should fail)
    response2 = await async_client.patch(
        f"/authors/{pending_author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"bio": "Trying to edit", "version": pending_author.version}
    )
    assert response2.status_code == 403


@pytest.mark.asyncio
async def test_owner_without_delete_scope_fails(async_client: AsyncClient, test_db: AsyncSession):
    """Test that owner without authors:delete_own scope cannot delete."""
    owner_id = uuid4()
    
    # Create author owned by user
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create owner JWT WITHOUT authors:delete_own scope
    jwt_token = create_test_jwt(
        user_id=owner_id,
        scopes=["authors:update_own"],  # Has update but not delete
        trust_score=15,
    )
    
    # Try to delete (should fail)
    response = await async_client.delete(
        f"/authors/{author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    assert response.status_code == 403
    assert "authors:delete_own" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_own_scope_only_works_on_owned(async_client: AsyncClient, test_db: AsyncSession):
    """Test that authors:update_own scope only works on user's own authors."""
    owner_id = uuid4()
    other_user_id = uuid4()
    
    # Create author owned by someone else
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create JWT for different user (has authors:update_own but not owner)
    jwt_token = create_test_jwt(
        user_id=other_user_id,
        scopes=["authors:update_own"],
        trust_score=15,
    )
    
    # Try to update (should fail)
    response = await async_client.patch(
        f"/authors/{author.id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"bio": "Updated bio", "version": author.version}
    )
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_permission_matrix_all_combinations(async_client: AsyncClient, test_db: AsyncSession):
    """Test all permission matrix combinations systematically."""
    owner_id = uuid4()
    other_user_id = uuid4()
    
    # Create APPROVED author
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    test_cases = [
        # (user_id, scopes, expected_status, description)
        (owner_id, ["authors:update_own"], 200, "Owner with update_own succeeds"),
        (owner_id, ["authors:edit_public_meta"], 200, "Owner with edit_public_meta succeeds"),
        (owner_id, [], 403, "Owner without any scope fails"),
        (other_user_id, ["authors:edit_public_meta"], 200, "Non-owner with wiki edit succeeds"),
        (other_user_id, ["authors:update_own"], 403, "Non-owner with only update_own fails"),
        (other_user_id, [], 403, "Non-owner without any scope fails"),
    ]
    
    for user_id, scopes, expected_status, description in test_cases:
        jwt_token = create_test_jwt(
            user_id=user_id,
            scopes=scopes,
            trust_score=25,
        )
        
        response = await async_client.patch(
            f"/authors/{author.id}",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"bio": f"Test: {description}", "version": author.version}
        )
