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
