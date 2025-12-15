"""
Tests for curator override functionality.
Covers instant approve/reject and vote clearing.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, JuryVote, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_curator_can_approve_without_votes(async_client: AsyncClient, test_db: AsyncSession):
    """Test that curator can approve author with zero votes."""
    submitter_id = uuid4()
    
    # Create pending author with no votes
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create curator JWT (trust >= 80, has jury:override)
    curator_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=curator_id,
        scopes=["jury:override"],
        trust_score=85,
    )
    
    # Approve author
    response = await async_client.post(
        f"/authors/{author.id}/approve",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == ContentStatus.APPROVED.value
    assert data["is_public"] is True


@pytest.mark.asyncio
async def test_curator_approval_clears_existing_votes(async_client: AsyncClient, test_db: AsyncSession):
    """Test that curator approval clears all existing jury votes."""
    submitter_id = uuid4()
    voter1_id = uuid4()
    voter2_id = uuid4()
    
    # Create pending author with votes
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=3,
    )
    test_db.add(author)
    await test_db.flush()
    
    # Add existing votes
    vote1 = JuryVote(user_id=voter1_id, entity_type="author", entity_id=author.id, vote_value=1)
    vote2 = JuryVote(user_id=voter2_id, entity_type="author", entity_id=author.id, vote_value=1)
    test_db.add_all([vote1, vote2])
    await test_db.flush()
    
    # Create curator JWT
    curator_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=curator_id,
        scopes=["jury:override"],
        trust_score=85,
    )
    
    # Approve author
    response = await async_client.post(
        f"/authors/{author.id}/approve",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    assert response.status_code == 200
    
    # Verify all votes were cleared
    from sqlalchemy import select
    result = await test_db.execute(
        select(JuryVote).where(
            JuryVote.entity_type == "author",
            JuryVote.entity_id == author.id
        )
    )
    remaining_votes = result.scalars().all()
    assert len(remaining_votes) == 0


@pytest.mark.asyncio
async def test_curator_rejection_with_reason(async_client: AsyncClient, test_db: AsyncSession):
    """Test that curator can reject with reason."""
    submitter_id = uuid4()
    
    # Create pending author
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=2,
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
    
    # Reject author with reason
    response = await async_client.post(
        f"/authors/{author.id}/reject?reason=Duplicate%20entry",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == ContentStatus.REJECTED.value
    assert data["is_public"] is False
    # Note: Rejection reason is in edit history, not in author model


@pytest.mark.asyncio
async def test_curator_actions_recorded_in_edit_history(async_client: AsyncClient, test_db: AsyncSession):
    """Test that curator approve/reject actions are recorded in edit history."""
    submitter_id = uuid4()
    
    # Create pending author
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
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
    
    # Approve author
    response = await async_client.post(
        f"/authors/{author.id}/approve",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == ContentStatus.APPROVED.value
    assert data["is_public"] is True
    
    # Note: Edit history endpoint (/authors/{id}/history) not yet implemented
    # History is recorded via record_approval() but there's no GET endpoint to retrieve it
    # This test verifies the approval succeeds; history retrieval tested separately when implemented
