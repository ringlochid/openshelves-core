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

    async def test_duplicate_review_fails(self, async_client, test_db, approved_book):
        """User cannot review same book twice."""
        from helpers.jwt_utils import create_test_jwt

        user_id = str(uuid4())
        jwt_token = create_test_jwt(
            user_id=user_id,
            scopes=["books:read"],
            trust_score=15,
        )

        # Create first review
        response1 = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"rating": 5, "comment": "Great book!"},
        )
        assert response1.status_code == 201

        # Try to create duplicate review
        response2 = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"rating": 4, "comment": "Another review"},
        )
        assert response2.status_code == 400
        assert "already" in response2.json()["detail"].lower()

    async def test_update_own_review(self, async_client, test_db, approved_book):
        """User can update their own review."""
        from helpers.jwt_utils import create_test_jwt

        user_id = str(uuid4())
        jwt_token = create_test_jwt(
            user_id=user_id,
            scopes=["books:read"],
            trust_score=15,
        )

        # Create review
        create_response = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"rating": 3, "comment": "It was okay"},
        )
        assert create_response.status_code == 201
        review_id = create_response.json()["id"]

        # Update review
        update_response = await async_client.patch(
            f"/books/reviews/{review_id}",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"rating": 5, "comment": "Actually it's great!"},
        )
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["rating"] == 5
        assert updated["comment"] == "Actually it's great!"

    async def test_cannot_update_others_review(
        self, async_client, test_db, approved_book
    ):
        """User cannot update someone else's review."""
        from helpers.jwt_utils import create_test_jwt

        # User 1 creates review
        user1_id = uuid4()
        jwt1 = create_test_jwt(user_id=user1_id, scopes=["books:read"], trust_score=15)

        create_response = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {jwt1}"},
            json={"rating": 4, "comment": "Good book"},
        )
        assert create_response.status_code == 201
        review_id = create_response.json()["id"]

        # User 2 tries to update User 1's review
        user2_id = uuid4()
        jwt2 = create_test_jwt(user_id=user2_id, scopes=["books:read"], trust_score=15)

        update_response = await async_client.patch(
            f"/books/reviews/{review_id}",
            headers={"Authorization": f"Bearer {jwt2}"},
            json={"rating": 1, "comment": "Terrible!"},
        )
        assert update_response.status_code == 403

    async def test_delete_own_review(self, async_client, test_db, approved_book):
        """User can delete their own review."""
        from helpers.jwt_utils import create_test_jwt

        user_id = str(uuid4())
        jwt_token = create_test_jwt(
            user_id=user_id, scopes=["books:read"], trust_score=15
        )

        # Create review
        create_response = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"rating": 5, "comment": "Excellent!"},
        )
        assert create_response.status_code == 201
        review_id = create_response.json()["id"]

        # Delete review
        delete_response = await async_client.delete(
            f"/books/reviews/{review_id}",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        assert delete_response.status_code == 204

        # Verify deleted (should be soft-deleted)
        get_response = await async_client.get(f"/books/{approved_book.id}/reviews")
        assert get_response.status_code == 200
        reviews = get_response.json()
        deleted_review = next((r for r in reviews if r["id"] == review_id), None)
        # Soft-deleted reviews should not appear in list
        assert deleted_review is None


@pytest.mark.asyncio
class TestReviewVoting:
    """Test review voting system with trust adjustments."""

    async def test_vote_helpful_increments_counter(
        self, async_client, test_db, approved_book
    ):
        """Trusted user can vote review as helpful."""
        from helpers.jwt_utils import create_test_jwt
        from models import VoteType

        # Create review
        reviewer_id = str(uuid4())
        reviewer_jwt = create_test_jwt(
            user_id=reviewer_id, scopes=["books:read"], trust_score=15
        )

        create_response = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {reviewer_jwt}"},
            json={"rating": 5, "comment": "Great book!"},
        )
        assert create_response.status_code == 201
        review_id = create_response.json()["id"]

        # Trusted user votes helpful
        voter_id = uuid4()
        voter_jwt = create_test_jwt(
            user_id=voter_id, scopes=["books:read"], trust_score=60
        )

        vote_response = await async_client.post(
            f"/books/reviews/{review_id}/vote",
            headers={"Authorization": f"Bearer {voter_jwt}"},
            json={"review_id": review_id, "vote": "HELPFUL"},
        )
        assert vote_response.status_code == 200
        result = vote_response.json()
        assert result["helpful_count"] == 1
        assert result["trust_delta"] == 1

    async def test_vote_unhelpful_increments_counter(
        self, async_client, test_db, approved_book
    ):
        """Trusted user can vote review as unhelpful."""
        from helpers.jwt_utils import create_test_jwt

        # Create review
        reviewer_id = str(uuid4())
        reviewer_jwt = create_test_jwt(
            user_id=reviewer_id, scopes=["books:read"], trust_score=15
        )

        create_response = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {reviewer_jwt}"},
            json={"rating": 5, "comment": "Great book!"},
        )
        assert create_response.status_code == 201
        review_id = create_response.json()["id"]

        # Trusted user votes unhelpful
        voter_id = str(uuid4())
        voter_jwt = create_test_jwt(
            user_id=voter_id, scopes=["books:read"], trust_score=60
        )

        vote_response = await async_client.post(
            f"/books/reviews/{review_id}/vote",
            headers={"Authorization": f"Bearer {voter_jwt}"},
            json={"review_id": review_id, "vote": "UNHELPFUL"},
        )
        assert vote_response.status_code == 200
        result = vote_response.json()
        assert result["unhelpful_count"] == 1
        assert result["trust_delta"] == -1

    # Legacy test
    # async def test_vote_requires_trust_50(self, async_client, test_db, approved_book):
    #     """Low trust user cannot vote on reviews."""
    #     from helpers.jwt_utils import create_test_jwt

    #     # Create review
    #     reviewer_id = str(uuid4())
    #     reviewer_jwt = create_test_jwt(user_id=reviewer_id, scopes=["books:read"], trust_score=15)

    #     create_response = await async_client.post(
    #         f"/books/{approved_book.id}/reviews",
    #         headers={"Authorization": f"Bearer {reviewer_jwt}"},
    #         json={"rating": 5, "comment": "Great book!"}
    #     )
    #     assert create_response.status_code == 201
    #     review_id = create_response.json()["id"]

    #     # Low trust user tries to vote
    #     low_trust_id = uuid4()
    #     low_trust_jwt = create_test_jwt(user_id=low_trust_id, scopes=["books:read"], trust_score=30)

    #     vote_response = await async_client.post(
    #         f"/books/reviews/{review_id}/vote",
    #         headers={"Authorization": f"Bearer {low_trust_jwt}"},
    #         json={"review_id": review_id, "vote": "HELPFUL"}
    #     )
    #     assert vote_response.status_code == 403

    async def test_vote_trust_cap_at_5(self, async_client, test_db, approved_book):
        """Review trust cannot exceed ±5."""
        from helpers.jwt_utils import create_test_jwt
        from models import Review
        from sqlalchemy import select, update

        # Create review
        reviewer_id = str(uuid4())
        reviewer_jwt = create_test_jwt(
            user_id=reviewer_id, scopes=["books:read"], trust_score=15
        )

        create_response = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {reviewer_jwt}"},
            json={"rating": 5, "comment": "Great book!"},
        )
        assert create_response.status_code == 201
        review_id = create_response.json()["id"]

        # Manually set helpful_count to 5 to reach cap (bypass API)
        await test_db.execute(
            update(Review)
            .where(Review.id == review_id)
            .values(helpful_count=5, unhelpful_count=0, trust_awarded=5)
        )
        await test_db.commit()

        # Vote should succeed even at cap (new behavior: votes always work)
        voter_id = uuid4()
        voter_jwt = create_test_jwt(
            user_id=voter_id, scopes=["books:read"], trust_score=60
        )

        vote_response = await async_client.post(
            f"/books/reviews/{review_id}/vote",
            headers={"Authorization": f"Bearer {voter_jwt}"},
            json={"review_id": review_id, "vote": "HELPFUL"},
        )
        assert vote_response.status_code == 200

        # Trust should remain capped at 5 (6 helpful - 0 unhelpful = 6, capped to 5)
        data = vote_response.json()
        assert data["helpful_count"] == 6
        assert data["unhelpful_count"] == 0
        assert data["trust_delta"] == 0  # No trust change because already at cap

        # Verify trust_awarded stayed at cap
        query = select(Review).where(Review.id == review_id)
        result = await test_db.execute(query)
        review = result.scalar_one()
        assert review.trust_awarded == 5  # Capped at 5 even with 6 helpful votes

    async def test_remove_vote_reverses_trust(
        self, async_client, test_db, approved_book
    ):
        """Removing vote reverses trust adjustment."""
        from helpers.jwt_utils import create_test_jwt

        # Create review
        reviewer_id = str(uuid4())
        reviewer_jwt = create_test_jwt(
            user_id=reviewer_id, scopes=["books:read"], trust_score=15
        )

        create_response = await async_client.post(
            f"/books/{approved_book.id}/reviews",
            headers={"Authorization": f"Bearer {reviewer_jwt}"},
            json={"rating": 5, "comment": "Great book!"},
        )
        assert create_response.status_code == 201
        review_id = create_response.json()["id"]

        # Vote helpful
        voter_id = str(uuid4())
        voter_jwt = create_test_jwt(
            user_id=voter_id, scopes=["books:read"], trust_score=60
        )

        vote_response = await async_client.post(
            f"/books/reviews/{review_id}/vote",
            headers={"Authorization": f"Bearer {voter_jwt}"},
            json={"review_id": review_id, "vote": "HELPFUL"},
        )
        assert vote_response.status_code == 200
        assert vote_response.json()["helpful_count"] == 1

        # Remove vote
        remove_response = await async_client.delete(
            f"/books/reviews/{review_id}/vote",
            headers={"Authorization": f"Bearer {voter_jwt}"},
        )
        assert remove_response.status_code == 204

        # Verify vote removed
        get_response = await async_client.get(f"/books/{approved_book.id}/reviews")
        reviews = get_response.json()
        review = next(r for r in reviews if r["id"] == review_id)
        assert review["helpful_count"] == 0
