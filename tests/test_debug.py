"""Quick debug test"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

from models import Author, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_debug_auth(async_client: AsyncClient, test_db: AsyncSession):
    """Debug test to see response details."""
    submitter_id = uuid4()
    author = Author(
        name="Test",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    jwt_token = create_test_jwt(uuid4(), ["jury:vote"], 15)
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"}
    )
    
    print(f"\nStatus: {response.status_code}")
    print(f"Response: {response.text}")
    print(f"Headers: {response.headers}")
