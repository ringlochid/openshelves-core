"""
Test book update functionality with version conflicts, permissions, and cache invalidation.
"""
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from models import ContentStatus


@pytest.mark.asyncio
class TestBookUpdate:
    """Test book update with versioning and permissions."""
    
    async def test_owner_can_update_pending_book(self, test_db, pending_book):
        """Owner can update their own PENDING book."""
        from routers.book import update_book
        from schemas.book import BookUpdate
        
        owner = {
            "user_id": str(pending_book.created_by_user_id),
            "scopes": ["books:update_own"],
        }
        
        old_version = pending_book.version  # Store before update
        data = BookUpdate(
            title="Updated Title",
            description="Updated description",
            version=old_version,
        )
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        book = await update_book(
                            book_id=pending_book.id,
                            data=data,
                            current_user=owner,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        assert book.title == "Updated Title"
        assert book.description == "Updated description"
        assert book.version == old_version + 1
    
    async def test_wiki_editor_can_update_approved_book(self, test_db, approved_book):
        """Wiki editor can update APPROVED book (not their own)."""
        from routers.book import update_book
        from schemas.book import BookUpdate
        
        wiki_editor = {
            "user_id": str(uuid4()),  # Different user
            "scopes": ["books:edit_public_meta"],
        }
        
        old_version = approved_book.version  # Store before update
        data = BookUpdate(
            description="Wiki-edited description",
            version=old_version,
        )
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        book = await update_book(
                            book_id=approved_book.id,
                            data=data,
                            current_user=wiki_editor,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        assert book.description == "Wiki-edited description"
        assert book.version == old_version + 1
    
    async def test_non_owner_cannot_update_pending_book(self, test_db, pending_book):
        """Non-owner cannot update PENDING book without wiki edit scope."""
        from routers.book import update_book
        from schemas.book import BookUpdate
        from fastapi import HTTPException
        
        other_user = {
            "user_id": str(uuid4()),
            "scopes": ["books:draft"],
        }
        
        data = BookUpdate(
            title="Hacked",
            version=pending_book.version,
        )
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await update_book(
                    book_id=pending_book.id,
                    data=data,
                    current_user=other_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 403
    
    async def test_version_conflict_detection(self, test_db, approved_book):
        """Update fails with version conflict if version mismatch."""
        from routers.book import update_book
        from schemas.book import BookUpdate
        from fastapi import HTTPException
        
        owner = {
            "user_id": str(approved_book.created_by_user_id),
            "scopes": ["books:update_own"],
        }
        
        # Provide wrong version
        data = BookUpdate(
            title="Update",
            version=99,  # Wrong version
        )
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await update_book(
                    book_id=approved_book.id,
                    data=data,
                    current_user=owner,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 409
    
    async def test_update_author_ids_invalidates_old_and_new(self, test_db, approved_book, approved_author):
        """Updating authors invalidates both OLD and NEW author IDs."""
        from routers.book import update_book
        from schemas.book import BookUpdate
        
        # Create second author
        from models import Author
        author2 = Author(
            name="Second Author",
            email="author2@example.com",
            created_by_user_id=uuid4(),
            status=ContentStatus.APPROVED,
            is_public=True,
        )
        test_db.add(author2)
        await test_db.flush()
        
        # Book currently has approved_author
        await test_db.refresh(approved_book, ["authors"])
        original_author_ids = [a.id for a in approved_book.authors]
        
        owner = {
            "user_id": str(approved_book.created_by_user_id),
            "scopes": ["books:update_own"],
        }
        
        data = BookUpdate(
            author_ids=[author2.id],  # Replace with different author
            version=approved_book.version,
        )
        
        mock_invalidate = AsyncMock()
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=mock_invalidate):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        book = await update_book(
                            book_id=approved_book.id,
                            data=data,
                            current_user=owner,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        # Verify cache invalidation called for OLD author AND NEW author
        # OLD: original_author_ids, NEW: [author2.id]
        called_author_ids = {call[0][0] for call in mock_invalidate.call_args_list}
        assert set(original_author_ids) | {author2.id} == called_author_ids
    
    async def test_cannot_update_deleted_book(self, test_db, deleted_book):
        """Cannot update soft-deleted book."""
        from routers.book import update_book
        from schemas.book import BookUpdate
        from fastapi import HTTPException
        
        owner = {
            "user_id": str(deleted_book.created_by_user_id),
            "scopes": ["books:update_own"],
        }
        
        data = BookUpdate(
            title="Update",
            version=deleted_book.version,
        )
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await update_book(
                    book_id=deleted_book.id,
                    data=data,
                    current_user=owner,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 410
