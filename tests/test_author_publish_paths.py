"""
Tests for direct publish paths (trusted user bypass).
Covers trusted vs regular user submission flows.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_trusted_user_direct_publish_approved(async_client: AsyncClient, test_db: AsyncSession):
    """Test that trusted user with authors:publish_direct creates APPROVED author."""
    # Create trusted user JWT (trust >= 50, has authors:publish_direct)
    trusted_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=trusted_id,
        scopes=["authors:draft", "authors:publish_direct"],
        trust_score=60,
    )
    
    # Create author
    response = await async_client.post(
        "/authors",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={
            "name": "Direct Publish Author",
            "email": "direct@example.com",
            "bio": "Trusted submitter",
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == ContentStatus.APPROVED.value
    assert data["is_public"] is True
    assert data["vote_score"] == 0  # Not using jury system


@pytest.mark.asyncio
async def test_regular_user_goes_to_pending(async_client: AsyncClient, test_db: AsyncSession):
    """Test that regular user without authors:publish_direct creates PENDING author."""
    # Create regular user JWT (no authors:publish_direct scope)
    user_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=user_id,
        scopes=["authors:draft"],
        trust_score=15,
    )
    
    # Create author
    response = await async_client.post(
        "/authors",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={
            "name": "Regular Submission",
            "email": "regular@example.com",
            "bio": "Needs jury approval",
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == ContentStatus.PENDING.value
    assert data["is_public"] is False
    assert data["vote_score"] == 0  # Starts at 0, jury will vote


@pytest.mark.asyncio
async def test_direct_publish_bypasses_jury_queue(async_client: AsyncClient, test_db: AsyncSession):
    """Test that direct-published authors don't appear in jury queue."""
    trusted_id = uuid4()
    contributor_id = uuid4()
    
    # Trusted user creates author
    trusted_jwt = create_test_jwt(
        user_id=trusted_id,
        scopes=["authors:draft", "authors:publish_direct"],
        trust_score=60,
    )
    
    response1 = await async_client.post(
        "/authors",
        headers={"Authorization": f"Bearer {trusted_jwt}"},
        json={
            "name": "Direct Author",
            "email": "direct@example.com",
        }
    )
    assert response1.status_code == 201
    
    # Regular user creates author
    regular_jwt = create_test_jwt(
        user_id=contributor_id,
        scopes=["authors:draft"],
        trust_score=15,
    )
    
    response2 = await async_client.post(
        "/authors",
        headers={"Authorization": f"Bearer {regular_jwt}"},
        json={
            "name": "Pending Author",
            "email": "pending@example.com",
        }
    )
    assert response2.status_code == 201
    
    # Check jury queue (should only have pending author)
    jury_jwt = create_test_jwt(
        user_id=uuid4(),
        scopes=["jury:view"],
        trust_score=15,
    )
    
    response3 = await async_client.get(
        "/jury/authors",
        headers={"Authorization": f"Bearer {jury_jwt}"}
    )
    
    assert response3.status_code == 200
    data = response3.json()
    # Should include our pending author
    assert data["total"] >= 1
    author_names = [item["name"] for item in data["items"]]
    assert "Pending Author" in author_names
    # Direct published author should NOT be in jury queue
    assert "Direct Author" not in author_names


@pytest.mark.asyncio
async def test_direct_publish_triggers_trust_adjustment(async_client: AsyncClient, test_db: AsyncSession, mocker):
    """Test that direct publish calls trust adjustment for submitter."""
    # Mock the Auth Service call
    mock_adjust_trust = mocker.patch(
        "routers.author.adjust_trust_for_approval",
        return_value=None
    )
    
    # Create trusted user JWT
    trusted_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=trusted_id,
        scopes=["authors:draft", "authors:publish_direct"],
        trust_score=60,
    )
    
    # Create author via direct publish
    response = await async_client.post(
        "/authors",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={
            "name": "Direct Publish Author",
            "email": "direct@example.com",
        }
    )
    
    assert response.status_code == 201
    author_id = response.json()["id"]
    
    # Verify trust adjustment was called
    mock_adjust_trust.assert_called_once()
    # Check call arguments (can be positional or keyword)
    call_args = mock_adjust_trust.call_args
    if call_args.args:
        # Positional args
        assert call_args.args[0] == trusted_id
        assert call_args.args[1] == "author"
        assert call_args.args[2] == author_id
        assert call_args.args[3] is False
    else:
        # Keyword args
        assert call_args.kwargs["user_id"] == trusted_id
        assert call_args.kwargs["entity_type"] == "author"
        assert call_args.kwargs["entity_id"] == author_id
        assert call_args.kwargs["is_book"] is False
