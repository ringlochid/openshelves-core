"""
Test book creation workflow with approval paths and direct publish.
"""
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from models import Book, Author, ContentStatus
from schemas.book import BookCreate
from routers.book import subscribe_to_book


@pytest.mark.asyncio
class TestBookCreation:
    """Test book creation with different user privileges."""
    
    async def test_create_book_pending_status(self, test_db, approved_author):
        """Regular user creates book → PENDING status."""
        from routers.book import create_book
        from cache import Redis
        
        # Regular user without direct publish
        user = {
            "user_id": str(uuid4()),
            "scopes": ["books:draft"],  # No publish_direct
            "trust_score": 5,
        }
        
        data = BookCreate(
            title="Test Book",
            year=2023,
            description="Test description",
            author_ids=[approved_author.id],
            tags=["fiction", "test"],
        )
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock(spec=Redis)):
            with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                    book = await create_book(
                        data=data,
                        current_user=user,
                        db=test_db,
                        r=AsyncMock(),
                    )
        
        assert book.status == ContentStatus.PENDING
        assert book.is_public is False
        assert book.version == 1
        assert str(book.created_by_user_id) == user["user_id"]
    
    async def test_create_book_direct_publish(self, test_db, approved_author):
        """Trusted user with direct publish → APPROVED immediately."""
        from routers.book import create_book
        
        # Trusted user with direct publish
        user = {
            "user_id": str(uuid4()),
            "scopes": ["books:draft", "books:publish_direct"],
            "trust_score": 60,
        }
        
        data = BookCreate(
            title="Direct Publish Book",
            year=2023,
            author_ids=[approved_author.id],
        )
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                    book = await create_book(
                        data=data,
                        current_user=user,
                        db=test_db,
                        r=AsyncMock(),
                    )
        
        assert book.status == ContentStatus.APPROVED
        assert book.is_public is True
        assert book.version == 1
    
    async def test_create_book_invalid_author_fails(self, test_db):
        """Creating book with non-existent author fails."""
        from routers.book import create_book
        from fastapi import HTTPException
        
        user = {
            "user_id": str(uuid4()),
            "scopes": ["books:draft"],
        }
        
        data = BookCreate(
            title="Invalid Book",
            year=2023,
            author_ids=[99999],  # Non-existent
        )
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await create_book(
                    data=data,
                    current_user=user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 400
        assert "invalid" in exc_info.value.detail.lower()


@pytest.mark.asyncio
class TestBookApproval:
    """Test curator book approval with trust rewards."""
    
    async def test_approve_book_trust_reward(self, test_db, pending_book, curator_user):
        """Approving book awards +20 trust (doubled for books)."""
        from routers.book import approve_book
        
        original_status = pending_book.status
        submitter_id = pending_book.created_by_user_id
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        with patch("routers.book.adjust_trust_for_approval") as mock_trust:
                            mock_trust.return_value = AsyncMock()
                            
                            book = await approve_book(
                                book_id=pending_book.id,
                                current_user=curator_user,
                                db=test_db,
                                r=AsyncMock(),
                            )
                            
                            # Verify trust call
                            mock_trust.assert_called_once()
                            call_args = mock_trust.call_args[1]
                            assert call_args["user_id"] == submitter_id
                            assert call_args["entity_type"] == "book"
                            assert call_args["entity_id"] == pending_book.id
                            assert call_args["is_book"] is True  # Doubles reward to +20
        
        assert book.status == ContentStatus.APPROVED
        assert book.is_public is True
        assert original_status == ContentStatus.PENDING
    
    async def test_approve_already_approved_fails(self, test_db, approved_book, curator_user):
        """Approving already approved book fails."""
        from routers.book import approve_book
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await approve_book(
                    book_id=approved_book.id,
                    current_user=curator_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 400
        assert "already approved" in exc_info.value.detail.lower()


@pytest.mark.asyncio
class TestBookRejection:
    """Test curator book rejection with trust penalties."""
    
    async def test_reject_book_trust_penalty(self, test_db, pending_book, curator_user):
        """Rejecting book applies -10 trust penalty (doubled for books)."""
        from routers.book import reject_book
        
        submitter_id = pending_book.created_by_user_id
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        with patch("routers.book.adjust_trust_for_rejection") as mock_trust:
                            mock_trust.return_value = AsyncMock()
                            
                            book = await reject_book(
                                book_id=pending_book.id,
                                reason="Quality issues",
                                current_user=curator_user,
                                db=test_db,
                                r=AsyncMock(),
                            )
                            
                            # Verify trust call
                            mock_trust.assert_called_once()
                            call_args = mock_trust.call_args[1]
                            assert call_args["user_id"] == submitter_id
                            assert call_args["reason"] == "Quality issues"
                            assert call_args["is_book"] is True  # Doubles penalty to -10
        
        assert book.status == ContentStatus.REJECTED
        assert book.is_public is False
    
    async def test_reject_already_rejected_fails(self, test_db, rejected_book, curator_user):
        """Rejecting already rejected book fails."""
        from routers.book import reject_book
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await reject_book(
                    book_id=rejected_book.id,
                    reason="Test",
                    current_user=curator_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 400
        assert "already rejected" in exc_info.value.detail.lower()


@pytest.mark.asyncio
class TestBookSoftDelete:
    """Test book soft deletion and recovery."""
    
    async def test_soft_delete_book(self, test_db, approved_book, curator_user):
        """Curator can soft delete book."""
        from routers.book import delete_book
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_author", new=AsyncMock()):
                with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                    with patch("routers.book.cache.bump_cache_version", new=AsyncMock()):
                        await delete_book(
                            book_id=approved_book.id,
                            current_user=curator_user,
                            db=test_db,
                            r=AsyncMock(),
                        )
        
        await test_db.refresh(approved_book)
        assert approved_book.is_deleted is True
        assert approved_book.deleted_at is not None
        assert approved_book.is_public is False
    
    async def test_delete_already_deleted_fails(self, test_db, deleted_book, curator_user):
        """Deleting already deleted book fails."""
        from routers.book import delete_book
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await delete_book(
                    book_id=deleted_book.id,
                    current_user=curator_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
class TestBookSubscription:
    """Test book subscription without trust rewards."""
    
    async def test_subscribe_to_book(self, test_db, approved_book, regular_user):
        """User can subscribe to approved book (no trust reward)."""
        from routers.book import subscribe_to_book
        
        original_count = approved_book.subscriber_count
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                result = await subscribe_to_book(
                    book_id=approved_book.id,
                    current_user=regular_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert "subscribed" in result["message"].lower()
        await test_db.refresh(approved_book)
        assert approved_book.subscriber_count == original_count + 1
    
    async def test_unsubscribe_from_book(self, test_db, approved_book, subscribed_user):
        """User can unsubscribe from book."""
        from routers.book import unsubscribe_from_book
        
        # First subscribe
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                await subscribe_to_book(
                    book_id=approved_book.id,
                    current_user=subscribed_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        count_after_sub = approved_book.subscriber_count
        
        # Then unsubscribe
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                await unsubscribe_from_book(
                    book_id=approved_book.id,
                    current_user=subscribed_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        await test_db.refresh(approved_book)
        assert approved_book.subscriber_count == count_after_sub - 1
    
    async def test_duplicate_subscription_fails(self, test_db, approved_book, regular_user):
        """Subscribing twice to same book fails."""
        from routers.book import subscribe_to_book
        from fastapi import HTTPException
        
        # First subscription
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_book", new=AsyncMock()):
                await subscribe_to_book(
                    book_id=approved_book.id,
                    current_user=regular_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        # Second subscription
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await subscribe_to_book(
                    book_id=approved_book.id,
                    current_user=regular_user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 400
        assert "already subscribed" in exc_info.value.detail.lower()
