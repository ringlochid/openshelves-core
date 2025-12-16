"""
Test review CRUD operations and voting system.
"""
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from models import ContentStatus


@pytest.mark.asyncio
class TestReviewCRUD:
    """Test review create, update, delete operations."""
    
    async def test_create_review_for_approved_book(self, test_db, approved_book):
        """User can create review for approved book."""
        from routers.book import create_review
        from schemas.review import ReviewCreate
        
        user = {
            "user_id": str(uuid4()),
            "scopes": ["books:read"],
        }
        
        data = ReviewCreate(
            rating=5,
            comment="Excellent book!",
        )
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_reviews", new=AsyncMock()):
                review = await create_review(
                    book_id=approved_book.id,
                    data=data,
                    current_user=user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert review.rating == 5
        assert review.comment == "Excellent book!"
        assert str(review.user_id) == user["user_id"]
        assert review.helpful_count == 0
        assert review.unhelpful_count == 0
    
    async def test_cannot_review_pending_book(self, test_db, pending_book):
        """Cannot create review for PENDING book."""
        from routers.book import create_review
        from schemas.review import ReviewCreate
        from fastapi import HTTPException
        
        user = {
            "user_id": str(uuid4()),
            "scopes": ["books:read"],
        }
        
        data = ReviewCreate(rating=5, comment="Great")
        
        with pytest.raises(HTTPException) as exc_info:
            with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
                await create_review(
                    book_id=pending_book.id,
                    data=data,
                    current_user=user,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        assert exc_info.value.status_code == 404
    
    @pytest.mark.skip(reason="Async loop issue - needs endpoint integration test")
    async def test_duplicate_review_fails(self, test_db, approved_book):
        """User cannot review same book twice."""
        pass
    
    @pytest.mark.skip(reason="Review update endpoint not yet implemented")
    async def test_update_own_review(self, test_db, approved_book):
        """User can update their own review."""
        pass
    
    @pytest.mark.skip(reason="Review update endpoint not yet implemented")
    async def test_cannot_update_others_review(self, test_db, approved_book):
        """User cannot update someone else's review."""
        pass
    
    @pytest.mark.skip(reason="Review delete endpoint not yet implemented")
    async def test_delete_own_review(self, test_db, approved_book):
        """User can delete their own review."""
        pass


@pytest.mark.asyncio
class TestReviewVoting:
    """Test review voting system with trust adjustments."""
    
    @pytest.mark.skip(reason="Review voting endpoints need implementation")
    async def test_vote_helpful_increments_counter(self, test_db, approved_book):
        """Trusted user can vote review as helpful."""
        from routers.book import create_review, vote_on_review
        from schemas.review import ReviewCreate, VoteRequest
        
        # Create review
        reviewer = {"user_id": str(uuid4()), "scopes": ["books:read"]}
        data = ReviewCreate(rating=5, comment="Great")
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_reviews", new=AsyncMock()):
                review = await create_review(
                    book_id=approved_book.id,
                    data=data,
                    current_user=reviewer,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        # Trusted user votes helpful
        voter = {
            "user_id": str(uuid4()),
            "scopes": ["books:read"],
            "trust_score": 60,  # Above 50 threshold
        }
        
        vote_data = VoteRequest(vote="helpful")
        
        with patch("routers.book.adjust_user_trust") as mock_trust:
            mock_trust.return_value = AsyncMock()
            
            result = await vote_on_review(
                review_id=review.id,
                data=vote_data,
                current_user=voter,
                db=test_db,
            )
        
        await test_db.refresh(review)
        assert review.helpful_count == 1
        assert review.trust_awarded == 1
        
        # Verify trust adjustment called for reviewer (+1)
        mock_trust.assert_called_once()
    
    @pytest.mark.skip(reason="Review voting endpoints need implementation")
    async def test_vote_unhelpful_increments_counter(self, test_db, approved_book):
        """Trusted user can vote review as unhelpful."""
        from routers.book import create_review, vote_on_review
        from schemas.review import ReviewCreate, VoteRequest
        
        # Create review
        reviewer = {"user_id": str(uuid4()), "scopes": ["books:read"]}
        data = ReviewCreate(rating=5, comment="Great")
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_reviews", new=AsyncMock()):
                review = await create_review(
                    book_id=approved_book.id,
                    data=data,
                    current_user=reviewer,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        # Trusted user votes unhelpful
        voter = {
            "user_id": str(uuid4()),
            "scopes": ["books:read"],
            "trust_score": 60,
        }
        
        vote_data = VoteRequest(vote="unhelpful")
        
        with patch("routers.book.adjust_user_trust") as mock_trust:
            mock_trust.return_value = AsyncMock()
            
            await vote_on_review(
                review_id=review.id,
                data=vote_data,
                current_user=voter,
                db=test_db,
            )
        
        await test_db.refresh(review)
        assert review.unhelpful_count == 1
        assert review.trust_awarded == -1
    
    @pytest.mark.skip(reason="Review voting endpoints need implementation")
    async def test_vote_requires_trust_50(self, test_db, approved_book):
        """Low trust user cannot vote on reviews."""
        from routers.book import create_review, vote_on_review
        from schemas.review import ReviewCreate, VoteRequest
        from fastapi import HTTPException
        
        # Create review
        reviewer = {"user_id": str(uuid4()), "scopes": ["books:read"]}
        data = ReviewCreate(rating=5, comment="Great")
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_reviews", new=AsyncMock()):
                review = await create_review(
                    book_id=approved_book.id,
                    data=data,
                    current_user=reviewer,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        # Low trust user
        low_trust = {
            "user_id": str(uuid4()),
            "scopes": ["books:read"],
            "trust_score": 30,  # Below 50 threshold
        }
        
        vote_data = VoteRequest(vote="helpful")
        
        with pytest.raises(HTTPException) as exc_info:
            await vote_on_review(
                review_id=review.id,
                data=vote_data,
                current_user=low_trust,
                db=test_db,
            )
        
        assert exc_info.value.status_code == 403
        assert "trust" in exc_info.value.detail.lower()
    
    @pytest.mark.skip(reason="Review voting endpoints need implementation")
    async def test_vote_trust_cap_at_5(self, test_db, approved_book):
        """Review trust cannot exceed ±5."""
        from routers.book import create_review, vote_on_review
        from schemas.review import ReviewCreate, VoteRequest
        from fastapi import HTTPException
        
        # Create review
        reviewer = {"user_id": str(uuid4()), "scopes": ["books:read"]}
        data = ReviewCreate(rating=5, comment="Great")
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_reviews", new=AsyncMock()):
                review = await create_review(
                    book_id=approved_book.id,
                    data=data,
                    current_user=reviewer,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        # Manually set trust_awarded to cap
        review.trust_awarded = 5
        await test_db.commit()
        
        # Try to vote again
        voter = {
            "user_id": str(uuid4()),
            "scopes": ["books:read"],
            "trust_score": 60,
        }
        
        vote_data = VoteRequest(vote="helpful")
        
        with pytest.raises(HTTPException) as exc_info:
            await vote_on_review(
                review_id=review.id,
                data=vote_data,
                current_user=voter,
                db=test_db,
            )
        
        assert exc_info.value.status_code == 400
        assert "cap" in exc_info.value.detail.lower() or "limit" in exc_info.value.detail.lower()
    
    @pytest.mark.skip(reason="Review voting endpoints need implementation")
    async def test_remove_vote_reverses_trust(self, test_db, approved_book):
        """Removing vote reverses trust adjustment."""
        from routers.book import create_review, vote_on_review, remove_review_vote
        from schemas.review import ReviewCreate, VoteRequest
        
        # Create review
        reviewer = {"user_id": str(uuid4()), "scopes": ["books:read"]}
        data = ReviewCreate(rating=5, comment="Great")
        
        with patch("routers.book.cache.get_redis", return_value=AsyncMock()):
            with patch("routers.book.cache.invalidate_reviews", new=AsyncMock()):
                review = await create_review(
                    book_id=approved_book.id,
                    data=data,
                    current_user=reviewer,
                    db=test_db,
                    r=AsyncMock(),
                )
        
        # Vote helpful
        voter = {
            "user_id": str(uuid4()),
            "scopes": ["books:read"],
            "trust_score": 60,
        }
        
        vote_data = VoteRequest(vote="helpful")
        
        with patch("routers.book.adjust_user_trust"):
            await vote_on_review(
                review_id=review.id,
                data=vote_data,
                current_user=voter,
                db=test_db,
            )
        
        await test_db.refresh(review)
        assert review.helpful_count == 1
        
        # Remove vote
        with patch("routers.book.adjust_user_trust") as mock_trust:
            mock_trust.return_value = AsyncMock()
            
            await remove_review_vote(
                review_id=review.id,
                current_user=voter,
                db=test_db,
            )
        
        await test_db.refresh(review)
        assert review.helpful_count == 0
        assert review.trust_awarded == 0
        
        # Verify trust reversed (-1 to undo the +1)
        mock_trust.assert_called_once()
