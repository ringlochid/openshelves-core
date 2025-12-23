"""
Tests for Collection endpoints.
Tests CRUD, book management, curator actions, and access control.
"""

import pytest
from uuid import uuid4
from fastapi import status
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from models import Collection, CollectionBook, Book, ContentStatus
from main import app
from database import get_async_db
from cache import get_redis
from dependencies.auth import get_current_user, get_current_user_optional
from helpers.jwt_utils import create_test_jwt


# ========================================
# TEST FIXTURES
# ========================================


class FakeRedis:
    """Minimal FakeRedis for collection tests."""

    def __init__(self):
        self.kv_store = {}
        self.hash_store = {}
        self.hyperloglog = {}

    async def hgetall(self, key):
        return self.hash_store.get(key, {})

    async def hset(self, key, mapping):
        self.hash_store.setdefault(key, {}).update(
            {k: str(v) for k, v in mapping.items()}
        )

    async def expire(self, key, seconds):
        return True

    async def set(self, key, value, ex=None):
        self.kv_store[key] = value
        return True

    async def get(self, key):
        return self.kv_store.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.kv_store.pop(key, None)
        return len(keys)

    async def exists(self, key):
        return 1 if key in self.kv_store else 0

    async def keys(self, pattern):
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in self.kv_store.keys() if k.startswith(prefix)]
        return [k for k in self.kv_store.keys() if k == pattern]

    async def pfadd(self, key, *values):
        """HyperLogLog add."""
        if key not in self.hyperloglog:
            self.hyperloglog[key] = set()
        added = 0
        for v in values:
            if v not in self.hyperloglog[key]:
                self.hyperloglog[key].add(v)
                added += 1
        return added

    async def pfcount(self, key):
        """HyperLogLog count."""
        return len(self.hyperloglog.get(key, set()))

    async def incr(self, key):
        """Increment a key."""
        current = self.kv_store.get(key, "0")
        if isinstance(current, bytes):
            current = current.decode()
        new_val = int(current) + 1
        self.kv_store[key] = str(new_val)
        return new_val

    async def aclose(self):
        pass


# ========================================
# PUBLIC ENDPOINT TESTS
# ========================================


@pytest.mark.asyncio
async def test_list_collections_returns_public_only(test_db, approved_collection):
    """Test that list_collections only returns approved, public collections."""
    fake_redis = FakeRedis()

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/collections")
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            assert "items" in data
            # Should find the approved collection
            items = data["items"]
            assert len(items) >= 1
            # All should be public
            for item in items:
                assert item["is_public"] == True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_collection_public_success(test_db, approved_collection):
    """Test that public collections are accessible anonymously."""
    fake_redis = FakeRedis()

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user_optional():
        return None  # Anonymous

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user_optional] = (
        override_get_current_user_optional
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/collections/{approved_collection.id}")
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            assert data["id"] == approved_collection.id
            assert data["name"] == approved_collection.name
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_pending_collection_owner_can_see(test_db, pending_collection):
    """Test that owners can see their pending collections."""
    fake_redis = FakeRedis()
    owner_id = str(pending_collection.created_by_user_id)

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user_optional():
        return {"user_id": owner_id, "scopes": []}

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user_optional] = (
        override_get_current_user_optional
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/collections/{pending_collection.id}")
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            assert data["id"] == pending_collection.id
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_pending_collection_non_owner_404(test_db, pending_collection):
    """Test that non-owners cannot see pending collections."""
    fake_redis = FakeRedis()
    other_user_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user_optional():
        return {"user_id": other_user_id, "scopes": []}

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user_optional] = (
        override_get_current_user_optional
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/collections/{pending_collection.id}")
            assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        app.dependency_overrides.clear()


# ========================================
# CREATE COLLECTION TESTS
# ========================================


@pytest.mark.asyncio
async def test_create_collection_success(test_db, approved_book):
    """Test creating a new collection."""
    fake_redis = FakeRedis()
    user_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": user_id,
            "scopes": ["collections:create"],
            "trust_score": 15,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/collections",
                json={
                    "name": "My Test Collection",
                    "description": "A great collection",
                    "book_ids": [approved_book.id],
                },
            )
            assert response.status_code == status.HTTP_201_CREATED

            data = response.json()
            assert data["name"] == "My Test Collection"
            assert data["status"] == "PENDING"
            assert data["is_public"] == False
            assert data["book_count"] == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_collection_with_publish_direct(test_db, approved_book):
    """Test that collections:publish_direct scope creates approved collection."""
    fake_redis = FakeRedis()
    user_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": user_id,
            "scopes": ["collections:create", "collections:publish_direct"],
            "trust_score": 50,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/collections",
                json={
                    "name": "Direct Publish Collection",
                    "description": "Bypasses jury",
                },
            )
            assert response.status_code == status.HTTP_201_CREATED

            data = response.json()
            assert data["status"] == "APPROVED"
            assert data["is_public"] == True
    finally:
        app.dependency_overrides.clear()


# ========================================
# UPDATE COLLECTION TESTS
# ========================================


@pytest.mark.asyncio
async def test_update_collection_by_owner(test_db, pending_collection):
    """Test owner can update their collection."""
    fake_redis = FakeRedis()
    owner_id = str(pending_collection.created_by_user_id)

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": owner_id,
            "scopes": ["collections:update_own"],
            "trust_score": 15,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/collections/{pending_collection.id}",
                json={
                    "name": "Updated Collection Name",
                    "version": pending_collection.version,
                },
            )
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            assert data["name"] == "Updated Collection Name"
            # CollectionRead doesn't include version, just verify update worked
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_update_collection_non_owner_forbidden(test_db, pending_collection):
    """Test non-owner cannot update collection."""
    fake_redis = FakeRedis()
    other_user_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": other_user_id,
            "scopes": ["collections:update_own"],
            "trust_score": 15,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/collections/{pending_collection.id}",
                json={
                    "name": "Hacked Name",
                    "version": pending_collection.version,
                },
            )
            assert response.status_code == status.HTTP_403_FORBIDDEN
    finally:
        app.dependency_overrides.clear()


# ========================================
# DELETE COLLECTION TESTS
# ========================================


@pytest.mark.asyncio
async def test_delete_collection_by_owner(test_db, pending_collection):
    """Test owner can delete their collection."""
    fake_redis = FakeRedis()
    owner_id = str(pending_collection.created_by_user_id)

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": owner_id,
            "scopes": ["collections:delete_own"],
            "trust_score": 15,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/collections/{pending_collection.id}")
            assert response.status_code == status.HTTP_204_NO_CONTENT
    finally:
        app.dependency_overrides.clear()


# ========================================
# BOOK MANAGEMENT TESTS
# ========================================


@pytest.mark.asyncio
async def test_add_book_to_collection(test_db, pending_collection, approved_book):
    """Test adding a book to a collection."""
    fake_redis = FakeRedis()
    owner_id = str(pending_collection.created_by_user_id)

    # Create another approved book to add
    from datetime import datetime, timezone

    new_book = Book(
        title="New Book To Add",
        year=2024,
        created_by_user_id=uuid4(),
        status=ContentStatus.APPROVED,
        is_public=True,
        version=1,
    )
    test_db.add(new_book)
    await test_db.commit()
    await test_db.refresh(new_book)

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": owner_id,
            "scopes": ["collections:update_own"],
            "trust_score": 15,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/collections/{pending_collection.id}/books",
                json={"book_id": new_book.id, "position": 2},
            )
            # Returns 200 OK with message
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            assert "position" in data
            assert "book_count" in data
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_remove_book_from_collection(test_db, pending_collection, approved_book):
    """Test removing a book from a collection."""
    fake_redis = FakeRedis()
    owner_id = str(pending_collection.created_by_user_id)

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": owner_id,
            "scopes": ["collections:update_own"],
            "trust_score": 15,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/collections/{pending_collection.id}/books/{approved_book.id}"
            )
            assert response.status_code == status.HTTP_204_NO_CONTENT
    finally:
        app.dependency_overrides.clear()


# ========================================
# SUBSCRIPTION TESTS
# ========================================


@pytest.mark.asyncio
async def test_subscribe_to_collection(test_db, approved_collection):
    """Test subscribing to a collection."""
    fake_redis = FakeRedis()
    user_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": user_id,
            "scopes": ["collections:subscribe"],
            "trust_score": 10,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/collections/{approved_collection.id}/subscribe"
            )
            # Returns 200 OK with subscription info
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            assert "user_id" in data or "message" in data
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unsubscribe_from_collection(test_db, approved_collection):
    """Test unsubscribing from a collection."""
    from models import CollectionSubscription
    from datetime import datetime, timezone

    fake_redis = FakeRedis()
    user_id = uuid4()

    # Create subscription first
    sub = CollectionSubscription(
        user_id=user_id,
        collection_id=approved_collection.id,
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(sub)
    approved_collection.subscriber_count = 1
    await test_db.commit()

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": str(user_id),
            "scopes": ["collections:subscribe"],
            "trust_score": 10,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/collections/{approved_collection.id}/subscribe"
            )
            assert response.status_code == status.HTTP_204_NO_CONTENT
    finally:
        app.dependency_overrides.clear()


# ========================================
# CURATOR TESTS
# ========================================


@pytest.mark.asyncio
async def test_curator_approve_collection(test_db, pending_collection):
    """Test curator can approve a collection."""
    fake_redis = FakeRedis()
    curator_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": curator_id,
            "scopes": ["jury:override"],
            "trust_score": 85,
        }

    # Mock trust adjustment
    from unittest.mock import patch, AsyncMock

    with patch(
        "routers.collection.adjust_trust_for_approval", new_callable=AsyncMock
    ) as mock_trust:
        mock_trust.return_value = None
        app.dependency_overrides[get_async_db] = override_get_db
        app.dependency_overrides[get_redis] = override_get_redis
        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/collections/{pending_collection.id}/approve"
                )
                assert response.status_code == status.HTTP_200_OK

                data = response.json()
                assert data["status"] == "APPROVED"
                assert data["is_public"] == True
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_curator_reject_collection(test_db, pending_collection):
    """Test curator can reject a collection."""
    fake_redis = FakeRedis()
    curator_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": curator_id,
            "scopes": ["jury:override"],
            "trust_score": 85,
        }

    # Mock trust adjustment
    from unittest.mock import patch, AsyncMock

    with patch(
        "routers.collection.adjust_trust_for_rejection", new_callable=AsyncMock
    ) as mock_trust:
        mock_trust.return_value = None
        app.dependency_overrides[get_async_db] = override_get_db
        app.dependency_overrides[get_redis] = override_get_redis
        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/collections/{pending_collection.id}/reject",
                    params={"reason": "Low quality content"},
                )
                assert response.status_code == status.HTTP_200_OK

                data = response.json()
                assert data["status"] == "REJECTED"
                assert data["is_public"] == False
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_curator_recover_collection(test_db, deleted_collection):
    """Test curator can recover a deleted collection."""
    fake_redis = FakeRedis()
    curator_id = str(uuid4())

    async def override_get_db():
        yield test_db

    async def override_get_redis():
        return fake_redis

    async def override_get_current_user():
        return {
            "user_id": curator_id,
            "scopes": ["jury:override"],  # recover requires jury:override
            "trust_score": 85,
        }

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/collections/{deleted_collection.id}/recover"
            )
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            assert data["is_deleted"] == False
    finally:
        app.dependency_overrides.clear()
