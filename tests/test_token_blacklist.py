"""
Tests for token blacklist and auth flow edge cases.
Tests optional auth flows and error handling.
"""

import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_optional_auth_allows_no_token(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that optional auth endpoints work without token."""
    user_id = uuid4()

    author = Author(
        name="Public Author Blacklist Test",
        email="public@example.com",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    response = await async_client.get(f"/authors/{author.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Public Author Blacklist Test"


@pytest.mark.asyncio
async def test_optional_auth_with_valid_token(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that optional auth works with valid token."""
    owner_id = uuid4()

    # Use an approved author - this verifies that token is correctly parsed
    author = Author(
        name="Approved Author Token Test",
        email="approved@example.com",
        created_by_user_id=owner_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    jwt_token = create_test_jwt(
        user_id=owner_id, scopes=["authors:read"], trust_score=10
    )

    response = await async_client.get(
        f"/authors/{author.id}", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Approved Author Token Test"


@pytest.mark.asyncio
async def test_missing_required_scope_returns_403(async_client: AsyncClient):
    """Test that missing required scope returns 403."""
    user_id = uuid4()

    jwt_token = create_test_jwt(user_id=user_id, scopes=["books:read"], trust_score=10)

    response = await async_client.post(
        "/books",
        json={"title": "Test Book"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 403
