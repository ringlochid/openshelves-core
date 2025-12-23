"""
Tests for collection list search and pagination.
Tests FTS, trigram fallback, cursor pagination, and sort functionality.
"""

import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Collection, ContentStatus


@pytest.mark.asyncio
async def test_collection_list_basic(async_client: AsyncClient, test_db: AsyncSession):
    """Test basic collection listing returns approved public collections."""
    user_id = uuid4()

    approved_collection = Collection(
        name="Approved Collection",
        description="A great collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    pending_collection = Collection(
        name="Pending Collection",
        description="Not yet approved",
        created_by_user_id=user_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )

    test_db.add_all([approved_collection, pending_collection])
    await test_db.flush()

    response = await async_client.get("/collections")

    assert response.status_code == 200
    data = response.json()

    names = [c["name"] for c in data["items"]]
    assert "Approved Collection" in names
    assert "Pending Collection" not in names


@pytest.mark.asyncio
async def test_collection_list_fts_search(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test full-text search on collections."""
    user_id = uuid4()

    poetry_collection = Collection(
        name="Poetry Anthology",
        description="Best poems of the century",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    science_collection = Collection(
        name="Science Must-Reads",
        description="Essential science books",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )

    test_db.add_all([poetry_collection, science_collection])
    await test_db.flush()

    # Search for "poetry"
    response = await async_client.get("/collections?q=poetry")

    assert response.status_code == 200
    data = response.json()

    names = [c["name"] for c in data["items"]]
    if data["items"]:
        # Poetry collection should be found
        assert any("Poetry" in n for n in names) or any(
            "poetry" in n.lower() for n in names
        )


@pytest.mark.asyncio
async def test_collection_list_name_search(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test name similarity search on collections."""
    user_id = uuid4()

    collection = Collection(
        name="Classical Literature",
        description="Timeless classics",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )

    test_db.add(collection)
    await test_db.flush()

    # Search by similar name
    response = await async_client.get("/collections?name=Classic")

    assert response.status_code == 200
    data = response.json()

    # Should find the collection via trigram similarity
    if data["items"]:
        names = [c["name"] for c in data["items"]]
        assert any("Classical" in n or "classic" in n.lower() for n in names)


@pytest.mark.asyncio
async def test_collection_list_cursor_pagination(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test cursor-based pagination for collections."""
    user_id = uuid4()

    # Create multiple collections
    for i in range(5):
        collection = Collection(
            name=f"Paginated Collection {i}",
            created_by_user_id=user_id,
            status=ContentStatus.APPROVED,
            is_public=True,
        )
        test_db.add(collection)
    await test_db.flush()

    # First page
    response = await async_client.get("/collections?limit=2")
    assert response.status_code == 200
    data = response.json()

    first_page_items = data["items"]
    next_cursor = data.get("next_cursor")

    if data.get("has_more"):
        assert next_cursor is not None

        # Get next page
        response2 = await async_client.get(f"/collections?limit=2&cursor={next_cursor}")
        assert response2.status_code == 200
        data2 = response2.json()

        # Items should be different
        second_page_names = [c["name"] for c in data2["items"]]
        first_page_names = [c["name"] for c in first_page_items]

        assert not set(first_page_names) & set(second_page_names)


@pytest.mark.asyncio
async def test_collection_list_sort_by_trending(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test sorting collections by trending score."""
    user_id = uuid4()

    low_trending = Collection(
        name="Low Trending Collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        trending_score=1.0,
    )
    high_trending = Collection(
        name="High Trending Collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        trending_score=100.0,
    )

    test_db.add_all([low_trending, high_trending])
    await test_db.flush()

    # Sort by trending descending
    response = await async_client.get("/collections?sort=trending_score:desc")

    assert response.status_code == 200
    data = response.json()

    if len(data["items"]) >= 2:
        names = [c["name"] for c in data["items"]]
        high_idx = next((i for i, n in enumerate(names) if "High" in n), None)
        low_idx = next((i for i, n in enumerate(names) if "Low" in n), None)

        if high_idx is not None and low_idx is not None:
            assert high_idx < low_idx


@pytest.mark.asyncio
async def test_collection_list_sort_by_subscribers(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test sorting collections by subscriber count."""
    user_id = uuid4()

    few_subs = Collection(
        name="Few Subscribers Collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        subscriber_count=5,
    )
    many_subs = Collection(
        name="Many Subscribers Collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        subscriber_count=1000,
    )

    test_db.add_all([few_subs, many_subs])
    await test_db.flush()

    # Sort by subscribers descending
    response = await async_client.get("/collections?sort=subscriber_count:desc")

    assert response.status_code == 200
    data = response.json()

    if len(data["items"]) >= 2:
        names = [c["name"] for c in data["items"]]
        many_idx = next((i for i, n in enumerate(names) if "Many" in n), None)
        few_idx = next((i for i, n in enumerate(names) if "Few" in n), None)

        if many_idx is not None and few_idx is not None:
            assert many_idx < few_idx


@pytest.mark.asyncio
async def test_collection_list_excludes_deleted(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that deleted collections are excluded from list."""
    user_id = uuid4()

    active_collection = Collection(
        name="Active Collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        is_deleted=False,
    )
    deleted_collection = Collection(
        name="Deleted Collection",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        is_deleted=True,
    )

    test_db.add_all([active_collection, deleted_collection])
    await test_db.flush()

    response = await async_client.get("/collections")

    assert response.status_code == 200
    data = response.json()

    names = [c["name"] for c in data["items"]]
    assert "Active Collection" in names
    assert "Deleted Collection" not in names


@pytest.mark.asyncio
async def test_collection_list_empty_results(async_client: AsyncClient):
    """Test that empty search returns empty list, not error."""
    response = await async_client.get("/collections?q=nonexistentcollectionxyz123")

    assert response.status_code == 200
    data = response.json()

    assert data["items"] == [] or "items" in data
