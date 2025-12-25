"""
Tests for author version rollback functionality.
"""

import pytest
from httpx import AsyncClient
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
class TestAuthorRollback:
    """Test suite for author version rollback endpoint."""

    async def test_rollback_restores_data(self, async_client: AsyncClient, test_db):
        """Test that rollback successfully restores data from a previous version."""
        from models import Author, EditHistory, ContentStatus, EditAction
        from datetime import datetime, timezone

        user_id = "550e8400-e29b-41d4-a716-446655440000"

        # Create author
        author = Author(
            name="Original Name",
            email="original@example.com",
            bio="Original bio",
            created_by_user_id=user_id,
            status=ContentStatus.APPROVED,
            is_public=True,
            version=1,
        )
        test_db.add(author)
        await test_db.flush()

        # Create edit history for version 1 (original)
        history_v1 = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.CREATE,
            user_id=user_id,
            version=1,
            new_data={
                "version": 1,
                "name": "Original Name",
                "email": "original@example.com",
                "bio": "Original bio",
            },
            changes={
                "total_changes": 3,
                "added": ["name", "email", "bio"],
                "modified": [],
                "removed": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history_v1)

        # Update author to version 2
        author.name = "Updated Name"
        author.email = "updated@example.com"
        author.version = 2
        author.last_edited_by = user_id
        author.last_edited_at = datetime.now(timezone.utc)

        # Create edit history for version 2
        history_v2 = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.UPDATE,
            user_id=user_id,
            version=2,
            parent_version=1,
            old_data={
                "version": 1,
                "name": "Original Name",
                "email": "original@example.com",
            },
            new_data={
                "version": 2,
                "name": "Updated Name",
                "email": "updated@example.com",
            },
            changes={
                "total_changes": 2,
                "added": [],
                "modified": [{"field": "name"}, {"field": "email"}],
                "removed": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history_v2)
        await test_db.commit()

        # Rollback to version 1
        token = create_test_jwt(
            user_id=user_id,
            scopes=["authors:update_own"],
        )

        response = await async_client.post(
            f"/authors/{author.id}/rollback",
            json={"target_version": 1, "version": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify data was restored
        assert data["name"] == "Original Name"
        assert data["email"] == "original@example.com"
        assert data["bio"] == "Original bio"

        # Version should increment (not revert to 1)
        assert data["version"] == 3

    async def test_rollback_permission_owner_can_rollback(
        self, async_client: AsyncClient, test_db
    ):
        """Test that owner with authors:update_own can rollback their own author."""
        from models import Author, EditHistory, ContentStatus, EditAction
        from datetime import datetime, timezone

        owner_id = "550e8400-e29b-41d4-a716-446655440000"

        # Create author
        author = Author(
            name="Test Author",
            email="test@example.com",
            created_by_user_id=owner_id,
            status=ContentStatus.PENDING,
            is_public=False,
            version=2,
        )
        test_db.add(author)
        await test_db.flush()

        # Add version 1 history
        history = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.CREATE,
            user_id=owner_id,
            version=1,
            new_data={"version": 1, "name": "Test Author", "email": "test@example.com"},
            changes={
                "total_changes": 2,
                "added": ["name", "email"],
                "modified": [],
                "removed": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        # Owner token
        token = create_test_jwt(
            user_id=owner_id,
            scopes=["authors:update_own"],
        )

        response = await async_client.post(
            f"/authors/{author.id}/rollback",
            json={"target_version": 1, "version": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

    async def test_rollback_permission_wiki_editor_can_rollback_approved(
        self, async_client: AsyncClient, test_db
    ):
        """Test that wiki editor can rollback APPROVED authors (not their own)."""
        from models import Author, EditHistory, ContentStatus, EditAction
        from datetime import datetime, timezone

        owner_id = "550e8400-e29b-41d4-a716-446655440000"
        editor_id = "550e8400-e29b-41d4-a716-446655440001"

        # Create APPROVED author by someone else
        author = Author(
            name="Test Author",
            email="test@example.com",
            created_by_user_id=owner_id,
            status=ContentStatus.APPROVED,
            is_public=True,
            version=2,
        )
        test_db.add(author)
        await test_db.flush()

        # Add version history
        history = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.CREATE,
            user_id=owner_id,
            version=1,
            new_data={"version": 1, "name": "Test Author", "email": "test@example.com"},
            changes={
                "total_changes": 2,
                "added": ["name", "email"],
                "modified": [],
                "removed": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        # Wiki editor token (not owner)
        token = create_test_jwt(
            user_id=editor_id,
            scopes=["authors:edit_public_meta"],
        )

        response = await async_client.post(
            f"/authors/{author.id}/rollback",
            json={"target_version": 1, "version": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

    async def test_rollback_permission_denied_non_owner_pending(
        self, async_client: AsyncClient, test_db
    ):
        """Test that non-owner cannot rollback PENDING authors."""
        from models import Author, EditHistory, ContentStatus, EditAction
        from datetime import datetime, timezone

        owner_id = "550e8400-e29b-41d4-a716-446655440000"
        other_user_id = "550e8400-e29b-41d4-a716-446655440001"

        # Create PENDING author
        author = Author(
            name="Test Author",
            email="test@example.com",
            created_by_user_id=owner_id,
            status=ContentStatus.PENDING,
            is_public=False,
            version=2,
        )
        test_db.add(author)
        await test_db.flush()

        # Add history
        history = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.CREATE,
            user_id=owner_id,
            version=1,
            new_data={"version": 1, "name": "Test Author"},
            changes={
                "total_changes": 1,
                "added": ["name"],
                "modified": [],
                "removed": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        # Non-owner token
        token = create_test_jwt(
            user_id=other_user_id,
            scopes=["authors:edit_public_meta"],
        )

        response = await async_client.post(
            f"/authors/{author.id}/rollback",
            json={"target_version": 1, "version": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    async def test_rollback_version_not_found(self, async_client: AsyncClient, test_db):
        """Test that rollback fails when target version doesn't exist."""
        from models import Author, ContentStatus

        user_id = "550e8400-e29b-41d4-a716-446655440000"

        # Create author without edit history
        author = Author(
            name="Test Author",
            email="test@example.com",
            created_by_user_id=user_id,
            status=ContentStatus.PENDING,
            is_public=False,
            version=2,
        )
        test_db.add(author)
        await test_db.commit()

        token = create_test_jwt(
            user_id=user_id,
            scopes=["authors:update_own"],
        )

        # Try to rollback to non-existent version
        response = await async_client.post(
            f"/authors/{author.id}/rollback",
            json={"target_version": 99, "version": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_rollback_creates_new_version(
        self, async_client: AsyncClient, test_db
    ):
        """Test that rollback creates a new version number (doesn't revert to old)."""
        from models import Author, EditHistory, ContentStatus, EditAction
        from datetime import datetime, timezone

        user_id = "550e8400-e29b-41d4-a716-446655440000"

        # Create author at version 3
        author = Author(
            name="Current Name",
            email="current@example.com",
            created_by_user_id=user_id,
            status=ContentStatus.APPROVED,
            is_public=True,
            version=3,
        )
        test_db.add(author)
        await test_db.flush()

        # Add version 1 history
        history_v1 = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.CREATE,
            user_id=user_id,
            version=1,
            new_data={"version": 1, "name": "Old Name", "email": "old@example.com"},
            changes={
                "total_changes": 2,
                "added": ["name", "email"],
                "modified": [],
                "removed": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history_v1)
        await test_db.commit()

        token = create_test_jwt(
            user_id=user_id,
            scopes=["authors:update_own"],
        )

        # Rollback to version 1
        response = await async_client.post(
            f"/authors/{author.id}/rollback",
            json={"target_version": 1, "version": 3},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # Should be version 4, not version 1
        assert data["version"] == 4
        assert data["name"] == "Old Name"

    async def test_rollback_version_conflict(self, async_client: AsyncClient, test_db):
        """Test that rollback fails with version conflict."""
        from models import Author, EditHistory, ContentStatus, EditAction
        from datetime import datetime, timezone

        user_id = "550e8400-e29b-41d4-a716-446655440000"

        # Create author at version 3
        author = Author(
            name="Test Author",
            email="test@example.com",
            created_by_user_id=user_id,
            status=ContentStatus.APPROVED,
            is_public=True,
            version=3,
        )
        test_db.add(author)
        await test_db.flush()

        # Add history
        history = EditHistory(
            entity_type="author",
            entity_id=author.id,
            action=EditAction.CREATE,
            user_id=user_id,
            version=1,
            new_data={"version": 1, "name": "Test Author"},
            changes={
                "total_changes": 1,
                "added": ["name"],
                "modified": [],
                "removed": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()

        token = create_test_jwt(
            user_id=user_id,
            scopes=["authors:update_own"],
        )

        # Try rollback with wrong version
        response = await async_client.post(
            f"/authors/{author.id}/rollback",
            json={"target_version": 1, "version": 2},  # Wrong: current is 3
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 409
        assert "conflict" in response.json()["detail"].lower()
