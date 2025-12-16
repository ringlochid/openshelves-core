"""
Test for author cache leak security vulnerability.

Ensures pending authors cached by jury endpoints don't leak to public endpoints.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_public_endpoint_rejects_pending_author_from_jury_cache(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """
    Regression test: Public GET /authors/{id} must return 404 for pending authors
    even if they were cached by a jury endpoint.
    
    Steps:
    1. Create pending author
    2. Jury viewer fetches author (caches it)
    3. Unauthenticated user tries to fetch same author
    4. Must get 404, not the cached pending data
    """
    # Step 1: Create pending author
    author = Author(
        name="Pending Author",
        email="pending@example.com",
        created_by_user_id=uuid4(),
        status=ContentStatus.PENDING,
        is_public=False,
    )
    test_db.add(author)
    await test_db.commit()
    await test_db.refresh(author)
    
    # Step 2: Jury viewer fetches pending author (this caches it)
    jury_jwt = create_test_jwt(
        user_id=uuid4(),
        scopes=["jury:view"],
        trust_score=50,
    )
    
    jury_response = await async_client.get(
        f"/jury/authors/{author.id}",
        headers={"Authorization": f"Bearer {jury_jwt}"}
    )
    assert jury_response.status_code == 200
    jury_data = jury_response.json()
    assert jury_data["status"] == "PENDING"
    
    # Step 3: Unauthenticated public request for same author
    public_response = await async_client.get(f"/authors/{author.id}")
    
    # Step 4: MUST return 404, not the cached pending author
    assert public_response.status_code == 404
    assert public_response.json()["detail"] == "Author not found"


@pytest.mark.asyncio
async def test_public_endpoint_returns_approved_author_from_cache(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """
    Verify that approved authors ARE correctly served from cache.
    """
    # Create approved, public author
    author = Author(
        name="Approved Author",
        email="approved@example.com",
        created_by_user_id=uuid4(),
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.commit()
    await test_db.refresh(author)
    
    # First request (populates cache)
    response1 = await async_client.get(f"/authors/{author.id}")
    assert response1.status_code == 200
    assert response1.json()["name"] == "Approved Author"
    
    # Second request (from cache)
    response2 = await async_client.get(f"/authors/{author.id}")
    assert response2.status_code == 200
    assert response2.json()["name"] == "Approved Author"
