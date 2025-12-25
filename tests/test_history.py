"""
Tests for edit history viewing endpoints.
Tests list endpoints for books/authors/collections and detail endpoint.
"""

import pytest
from uuid import uuid4
from datetime import datetime, timezone
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, Book, Collection, EditHistory, EditAction, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
class TestBookHistoryEndpoint:
    """Test GET /books/{id}/history endpoint."""

    async def test_public_book_history_accessible_without_auth(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """Anyone can view history of a public book."""
        # Create public book
        book = Book(
            title="Public Book",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,
            is_public=True,
            version=1,
        )
        test_db.add(book)
        await test_db.flush()

        # Create history record
        history = EditHistory(
            entity_type="book",
            entity_id=book.id,
            action=EditAction.CREATE,
            user_id=book.created_by_user_id,
            version=1,
            new_data={"title": "Public Book"},
            changes={
                "added": ["title"],
                "modified": [],
                "removed": [],
                "total_changes": 1,
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        # No auth - should succeed for public book
        response = await async_client.get(f"/books/{book.id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["version"] == 1
        assert data["items"][0]["added_count"] == 1

    async def test_private_book_history_requires_ownership(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """Private book history only accessible by owner."""
        owner_id = uuid4()
        other_user_id = uuid4()

        # Create private book
        book = Book(
            title="Private Book",
            created_by_user_id=owner_id,
            status=ContentStatus.PENDING,
            is_public=False,
            version=1,
        )
        test_db.add(book)
        await test_db.flush()

        # Create history
        history = EditHistory(
            entity_type="book",
            entity_id=book.id,
            action=EditAction.CREATE,
            user_id=owner_id,
            version=1,
            new_data={"title": "Private Book"},
            changes={
                "added": ["title"],
                "modified": [],
                "removed": [],
                "total_changes": 1,
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        # Other user without auth - should fail
        response = await async_client.get(f"/books/{book.id}/history")
        assert response.status_code == 403

        # Owner - should succeed
        token = create_test_jwt(user_id=owner_id, scopes=[])
        response = await async_client.get(
            f"/books/{book.id}/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    async def test_jury_can_view_any_book_history(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """Jury member with jury:view can see any book's history."""
        owner_id = uuid4()
        jury_user_id = uuid4()

        # Create private pending book
        book = Book(
            title="Pending Book",
            created_by_user_id=owner_id,
            status=ContentStatus.PENDING,
            is_public=False,
            version=1,
        )
        test_db.add(book)
        await test_db.flush()

        # Create history
        history = EditHistory(
            entity_type="book",
            entity_id=book.id,
            action=EditAction.CREATE,
            user_id=owner_id,
            version=1,
            new_data={"title": "Pending Book"},
            changes={
                "added": ["title"],
                "modified": [],
                "removed": [],
                "total_changes": 1,
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        # Jury member - should succeed
        token = create_test_jwt(user_id=jury_user_id, scopes=["jury:view"])
        response = await async_client.get(
            f"/books/{book.id}/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
class TestAuthorHistoryEndpoint:
    """Test GET /authors/{id}/history endpoint."""

    async def test_public_author_history_accessible(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """Anyone can view history of a public author."""
        author = Author(
            name="Public Author",
            email="public@example.com",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,
            is_public=True,
            version=1,
        )
        test_db.add(author)
        await test_db.flush()

        history = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.CREATE,
            user_id=author.created_by_user_id,
            version=1,
            new_data={"name": "Public Author"},
            changes={
                "added": ["name"],
                "modified": [],
                "removed": [],
                "total_changes": 1,
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        response = await async_client.get(f"/authors/{author.id}/history")
        assert response.status_code == 200
        assert response.json()["total"] == 1


@pytest.mark.asyncio
class TestCollectionHistoryEndpoint:
    """Test GET /collections/{id}/history endpoint."""

    async def test_public_collection_history_accessible(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """Anyone can view history of a public collection."""
        collection = Collection(
            name="Public Collection",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,
            is_public=True,
            version=1,
        )
        test_db.add(collection)
        await test_db.flush()

        history = EditHistory(
            entity_type="collection",
            entity_id=collection.id,
            action=EditAction.CREATE,
            user_id=collection.created_by_user_id,
            version=1,
            new_data={"name": "Public Collection"},
            changes={
                "added": ["name"],
                "modified": [],
                "removed": [],
                "total_changes": 1,
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        response = await async_client.get(f"/collections/{collection.id}/history")
        assert response.status_code == 200
        assert response.json()["total"] == 1


@pytest.mark.asyncio
class TestHistoryDetailEndpoint:
    """Test GET /history/{id} endpoint."""

    async def test_get_history_detail_for_public_entity(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """Can get full history detail for public entity."""
        book = Book(
            title="Detail Test Book",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,
            is_public=True,
            version=2,
        )
        test_db.add(book)
        await test_db.flush()

        # Create history with old/new data
        history = EditHistory(
            entity_type="book",
            entity_id=book.id,
            action=EditAction.UPDATE,
            user_id=book.created_by_user_id,
            version=2,
            parent_version=1,
            old_data={"title": "Old Title", "version": 1},
            new_data={"title": "Detail Test Book", "version": 2},
            changes={
                "added": [],
                "modified": [
                    {
                        "field": "title",
                        "old_value": "Old Title",
                        "new_value": "Detail Test Book",
                    }
                ],
                "removed": [],
                "total_changes": 1,
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        response = await async_client.get(f"/history/{history.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == history.id
        assert data["action"] == "UPDATE"
        assert data["old_data"]["title"] == "Old Title"
        assert data["new_data"]["title"] == "Detail Test Book"
        assert data["changes"]["modified"][0]["field"] == "title"

    async def test_history_detail_not_found(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """404 for non-existent history record."""
        response = await async_client.get("/history/999999")
        assert response.status_code == 404


@pytest.mark.asyncio
class TestHistoryPagination:
    """Test pagination for history list endpoints."""

    async def test_pagination_works(
        self, async_client: AsyncClient, test_db: AsyncSession
    ):
        """Verify pagination params work correctly."""
        book = Book(
            title="Paginated Book",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,
            is_public=True,
            version=5,
        )
        test_db.add(book)
        await test_db.flush()

        # Create 5 history records
        for v in range(1, 6):
            history = EditHistory(
                entity_type="book",
                entity_id=book.id,
                action=EditAction.CREATE if v == 1 else EditAction.UPDATE,
                user_id=book.created_by_user_id,
                version=v,
                new_data={"title": f"Version {v}"},
                changes={
                    "added": [],
                    "modified": [],
                    "removed": [],
                    "total_changes": 1,
                },
                created_at=datetime.now(timezone.utc),
            )
            test_db.add(history)
        await test_db.commit()

        # Request page 1 with per_page=2
        response = await async_client.get(f"/books/{book.id}/history?page=1&per_page=2")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert data["pages"] == 3  # ceil(5/2)

        # Request page 3
        response = await async_client.get(f"/books/{book.id}/history?page=3&per_page=2")
        data = response.json()
        assert len(data["items"]) == 1  # Last page has 1 item
