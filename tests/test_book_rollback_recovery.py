"""
Test book rollback and recovery functionality.
"""
import pytest
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from models import ContentStatus, EditHistory, EditAction


@pytest.mark.asyncio
class TestBookRollback:
    """Test book rollback to previous versions."""
    
    async def test_rollback_restores_previous_version(self, test_db, approved_book):
        """Rollback restores data from a previous version."""
        from routers.book import rollback_book_version
        from schemas.book import RollbackRequest
        
        # Create edit history for version 1
        history = EditHistory(
            entity_type="book",
            entity_id=approved_book.id,
            action=EditAction.CREATE,
            user_id=approved_book.created_by_user_id,
            version=1,
            new_data={
                "version": 1,
                "title": "Original Title",
                "year": 2020,
                "description": "Original description",
            },
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()
        
        # Update book to version 2
        approved_book.title = "Updated Title"
        approved_book.version = 2
        await test_db.commit()
        
        owner = {
            "user_id": str(approved_book.created_by_user_id),
            "scopes": ["books:update_own"],
        }
        
        data = RollbackRequest(target_version=1, version=2)
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        book = await rollback_book_version(
                            book_id=approved_book.id,
                            data=data,
                            current_user=owner,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        assert book.title == "Original Title"
        assert book.year == 2020
        assert book.description == "Original description"
        assert book.version == 3  # Incremented, not reverted to 1
    
    async def test_rollback_version_conflict(self, test_db, approved_book):
        """Cannot rollback to version >= current version."""
        from routers.book import rollback_book_version
        from schemas.book import RollbackRequest
        from fastapi import HTTPException
        
        owner = {
            "user_id": str(approved_book.created_by_user_id),
            "scopes": ["books:update_own"],
        }
        
        data = RollbackRequest(target_version=99, version=approved_book.version)
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await rollback_book_version(
                    book_id=approved_book.id,
                    data=data,
                    current_user=owner,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()
    
    async def test_wiki_editor_can_rollback_approved_book(self, test_db, approved_book):
        """Wiki editor can rollback APPROVED book (not their own)."""
        from routers.book import rollback_book_version
        from schemas.book import RollbackRequest
        
        # Create edit history
        history = EditHistory(
            entity_type="book",
            entity_id=approved_book.id,
            action=EditAction.CREATE,
            user_id=approved_book.created_by_user_id,
            version=1,
            new_data={"version": 1, "title": "Version 1"},
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(history)
        await test_db.commit()
        
        # Update book to version 2 so we can rollback to version 1
        approved_book.title = "Updated"
        approved_book.version = 2
        await test_db.commit()
        
        wiki_editor = {
            "user_id": str(uuid4()),
            "scopes": ["books:edit_public_meta"],
        }
        
        data = RollbackRequest(target_version=1, version=2)
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        book = await rollback_book_version(
                            book_id=approved_book.id,
                            data=data,
                            current_user=wiki_editor,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        # Rollback increments version to 3 (was 2, rollback to 1 → becomes 3)
        assert book.version == 3


@pytest.mark.asyncio
class TestBookRecovery:
    """Test book recovery from soft deletion."""
    
    async def test_recover_within_24h_window(self, test_db, deleted_book, curator_user):
        """Curator can recover book deleted within 24 hours."""
        from routers.book import recover_deleted_book
        
        # Ensure deletion is recent
        deleted_book.deleted_at = datetime.now(timezone.utc) - timedelta(hours=12)
        await test_db.commit()
        
        old_version = deleted_book.version  # Store before recovery
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        book = await recover_deleted_book(
                            book_id=deleted_book.id,
                            current_user=curator_user,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        assert book.is_deleted is False
        assert book.deleted_at is None
        assert book.version == old_version + 1
    
    async def test_recover_after_24h_fails(self, test_db, deleted_book, curator_user):
        """Cannot recover book deleted more than 24 hours ago."""
        from routers.book import recover_deleted_book
        from fastapi import HTTPException
        
        # Set deletion to 25 hours ago
        deleted_book.deleted_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await test_db.commit()
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await recover_deleted_book(
                    book_id=deleted_book.id,
                    current_user=curator_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 410
        assert "24 hours" in exc_info.value.detail.lower()
    
    async def test_recover_non_deleted_fails(self, test_db, approved_book, curator_user):
        """Cannot recover book that is not deleted."""
        from routers.book import recover_deleted_book
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await recover_deleted_book(
                    book_id=approved_book.id,
                    current_user=curator_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 400
        assert "not deleted" in exc_info.value.detail.lower()
    
    async def test_recover_restores_visibility_based_on_status(self, test_db, curator_user):
        """Recovery restores is_public based on approval status."""
        from routers.book import recover_deleted_book
        from models import Book, Author
        
        # Create approved deleted book
        author = Author(
            name="Test Author",
            email="test@example.com",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,
            is_public=True,
        )
        test_db.add(author)
        await test_db.flush()
        
        book = Book(
            title="Approved Deleted Book",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,  # Was approved
            is_public=False,  # Hidden due to deletion
            is_deleted=True,
            deleted_at=datetime.now(timezone.utc) - timedelta(hours=1),
            version=1,
        )
        book.authors = [author]
        test_db.add(book)
        await test_db.flush()
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        recovered = await recover_deleted_book(
                            book_id=book.id,
                            current_user=curator_user,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        # Should be public again since status is APPROVED
        assert recovered.is_public is True
        assert recovered.status == ContentStatus.APPROVED
