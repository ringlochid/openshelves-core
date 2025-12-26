"""
Tests for Auth Service client and trust adjustment.
Tests mocked calls to the Auth Service for trust adjustments.
"""

import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, Book, Collection, ContentStatus
from helpers.jwt_utils import create_test_jwt
from services.auth_client import (
    auth_service_client,
    adjust_trust_for_approval,
    adjust_trust_for_rejection,
)


@pytest.mark.asyncio
async def test_adjust_trust_for_approval_called_on_curator_approve(
    async_client: AsyncClient, test_db: AsyncSession, mocker
):
    """Test that trust adjustment is called when curator approves."""
    mock_adjust = mocker.patch(
        "routers.author.adjust_trust_for_approval", new_callable=AsyncMock
    )

    submitter_id = uuid4()
    curator_id = uuid4()

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

    jwt_token = create_test_jwt(
        user_id=curator_id, scopes=["jury:override"], trust_score=85
    )

    response = await async_client.post(
        f"/authors/{author.id}/approve",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200

    # Verify trust adjustment was called
    mock_adjust.assert_called_once()
    call_args = mock_adjust.call_args
    assert call_args.kwargs["user_id"] == submitter_id
    assert call_args.kwargs["entity_type"] == "author"
    assert call_args.kwargs["entity_id"] == author.id


@pytest.mark.asyncio
async def test_adjust_trust_for_rejection_called_on_curator_reject(
    async_client: AsyncClient, test_db: AsyncSession, mocker
):
    """Test that trust adjustment is called when curator rejects."""
    mock_adjust = mocker.patch(
        "routers.author.adjust_trust_for_rejection", new_callable=AsyncMock
    )

    submitter_id = uuid4()
    curator_id = uuid4()

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

    jwt_token = create_test_jwt(
        user_id=curator_id, scopes=["jury:override"], trust_score=85
    )

    response = await async_client.post(
        f"/authors/{author.id}/reject?reason=Low+quality",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200

    # Verify trust adjustment was called
    mock_adjust.assert_called_once()
    call_args = mock_adjust.call_args
    assert call_args.kwargs["user_id"] == submitter_id


@pytest.mark.asyncio
async def test_trust_adjustment_failure_does_not_block_approval(
    async_client: AsyncClient, test_db: AsyncSession, mocker
):
    """Test that approval succeeds even if trust adjustment fails."""
    # Mock trust adjustment to raise exception
    mocker.patch(
        "routers.author.adjust_trust_for_approval",
        new_callable=AsyncMock,
        side_effect=Exception("Auth service unavailable"),
    )

    submitter_id = uuid4()
    curator_id = uuid4()

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

    jwt_token = create_test_jwt(
        user_id=curator_id, scopes=["jury:override"], trust_score=85
    )

    # Approval should still succeed
    response = await async_client.post(
        f"/authors/{author.id}/approve",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200

    # Verify author is approved despite trust adjustment failure
    await test_db.refresh(author)
    assert author.status == ContentStatus.APPROVED


@pytest.mark.asyncio
async def test_book_approval_uses_is_book_flag(
    async_client: AsyncClient, test_db: AsyncSession, mocker
):
    """Test that book approval passes is_book=True for doubled trust."""
    mock_adjust = mocker.patch(
        "routers.book.adjust_trust_for_approval", new_callable=AsyncMock
    )

    submitter_id = uuid4()
    curator_id = uuid4()

    book = Book(
        title="Test Book",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    test_db.add(book)
    await test_db.flush()
    await test_db.refresh(book)

    jwt_token = create_test_jwt(
        user_id=curator_id, scopes=["jury:override"], trust_score=85
    )

    response = await async_client.post(
        f"/books/{book.id}/approve", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200

    # Verify is_book flag was passed
    mock_adjust.assert_called_once()
    call_args = mock_adjust.call_args
    assert call_args.kwargs.get("is_book") is True


@pytest.mark.asyncio
async def test_jury_auto_publish_calls_trust_adjustment(
    async_client: AsyncClient, test_db: AsyncSession, mocker
):
    """Test that auto-publish on vote threshold calls trust adjustment."""
    mock_adjust = mocker.patch(
        "helpers.jury.adjust_trust_for_approval", new_callable=AsyncMock
    )

    submitter_id = uuid4()
    voter_id = uuid4()

    # Create author with score near threshold
    author = Author(
        name="Near Threshold Author",
        email="near@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=4,  # One vote away
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    jwt_token = create_test_jwt(user_id=voter_id, scopes=["jury:vote"], trust_score=15)

    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200

    # Verify trust adjustment was called for auto-publish
    mock_adjust.assert_called_once()
    call_args = mock_adjust.call_args
    assert call_args.kwargs["user_id"] == submitter_id
    assert call_args.kwargs["entity_type"] == "author"


# ============================================================
# Tests for new submission adjustment / reputation integration
# ============================================================


@pytest.mark.asyncio
async def test_adjust_user_submissions_method(mocker):
    """Test that adjust_user_submissions calls correct endpoint."""
    from unittest.mock import MagicMock

    user_id = uuid4()

    # Mock httpx response - use MagicMock since json() is synchronous
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "user_id": str(user_id),
        "trust_score": 50,
        "reputation_percentage": 85.5,
        "roles": ["user", "contributor"],
        "pending_upgrade": None,
        "is_blacklisted": False,
        "is_locked": False,
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    mocker.patch("httpx.AsyncClient", return_value=mock_client)

    result = await auth_service_client.adjust_user_submissions(
        user_id=user_id,
        total_delta=1,
        successful_delta=1,
        reason="Test submission",
        source="upload",
    )

    # Verify the endpoint was called with correct payload
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert f"submissions/adjust" in call_args.args[0]
    payload = call_args.kwargs["json"]
    assert payload["total_delta"] == 1
    assert payload["successful_delta"] == 1
    assert payload["reason"] == "Test submission"
    assert payload["source"] == "upload"

    # Verify response
    assert result["reputation_percentage"] == 85.5


@pytest.mark.asyncio
async def test_adjust_trust_for_approval_calls_both_endpoints(mocker):
    """Test that approval calls submissions then trust endpoints."""
    user_id = uuid4()

    # Mock both client methods
    mock_submissions = mocker.patch.object(
        auth_service_client,
        "adjust_user_submissions",
        new_callable=AsyncMock,
        return_value={"reputation_percentage": 90.0},
    )
    mock_trust = mocker.patch.object(
        auth_service_client,
        "adjust_user_trust",
        new_callable=AsyncMock,
        return_value={"trust_score": 60},
    )

    await adjust_trust_for_approval(
        user_id=user_id,
        entity_type="author",
        entity_id=123,
        is_book=False,
    )

    # Verify submissions was called first with successful=1
    mock_submissions.assert_called_once()
    sub_args = mock_submissions.call_args
    assert sub_args.kwargs["user_id"] == user_id
    assert sub_args.kwargs["total_delta"] == 1
    assert sub_args.kwargs["successful_delta"] == 1
    assert "approved" in sub_args.kwargs["reason"].lower()

    # Verify trust was called with positive delta
    mock_trust.assert_called_once()
    trust_args = mock_trust.call_args
    assert trust_args.kwargs["user_id"] == user_id
    assert trust_args.kwargs["delta"] == 10  # Not a book


@pytest.mark.asyncio
async def test_adjust_trust_for_rejection_calls_both_endpoints(mocker):
    """Test that rejection calls submissions then trust endpoints."""
    user_id = uuid4()

    # Mock both client methods
    mock_submissions = mocker.patch.object(
        auth_service_client,
        "adjust_user_submissions",
        new_callable=AsyncMock,
        return_value={"reputation_percentage": 75.0},
    )
    mock_trust = mocker.patch.object(
        auth_service_client,
        "adjust_user_trust",
        new_callable=AsyncMock,
        return_value={"trust_score": 40},
    )

    await adjust_trust_for_rejection(
        user_id=user_id,
        entity_type="book",
        entity_id=456,
        reason="Low quality",
        is_book=True,
    )

    # Verify submissions was called with successful=0 (failure)
    mock_submissions.assert_called_once()
    sub_args = mock_submissions.call_args
    assert sub_args.kwargs["user_id"] == user_id
    assert sub_args.kwargs["total_delta"] == 1
    assert sub_args.kwargs["successful_delta"] == 0  # Failed submission
    assert "rejected" in sub_args.kwargs["reason"].lower()

    # Verify trust was called with negative delta (doubled for book)
    mock_trust.assert_called_once()
    trust_args = mock_trust.call_args
    assert trust_args.kwargs["user_id"] == user_id
    assert trust_args.kwargs["delta"] == -10  # Book penalty


@pytest.mark.asyncio
async def test_book_approval_gets_doubled_trust_reward(mocker):
    """Test that book approval gets 20 trust points (doubled)."""
    user_id = uuid4()

    mock_submissions = mocker.patch.object(
        auth_service_client, "adjust_user_submissions", new_callable=AsyncMock
    )
    mock_trust = mocker.patch.object(
        auth_service_client, "adjust_user_trust", new_callable=AsyncMock
    )

    await adjust_trust_for_approval(
        user_id=user_id,
        entity_type="book",
        entity_id=789,
        is_book=True,
    )

    # Verify trust delta is 20 for books
    trust_args = mock_trust.call_args
    assert trust_args.kwargs["delta"] == 20
