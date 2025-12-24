"""
Tests for jury voting system (democratic content approval).
Tests the complete voting workflow including auto-publish.
"""

import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, JuryVote, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_contributor_vote_weight_is_one(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that contributor vote has weight +1."""
    # Create pending author
    submitter_id = uuid4()
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    # Create contributor JWT (trust >= 10, has jury:vote)
    contributor_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:view", "jury:vote"],
        trust_score=15,
    )

    # Cast vote
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["vote_weight"] == 1
    assert data["new_vote_score"] == 1
    assert data["auto_approved"] is False


@pytest.mark.asyncio
async def test_trusted_vote_weight_is_five(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that trusted user vote has weight +5."""
    # Create pending author
    submitter_id = uuid4()
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    # Create trusted user JWT (trust >= 50, has jury:vote_weighted)
    trusted_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=trusted_id,
        scopes=["jury:view", "jury:vote", "jury:vote_weighted"],
        trust_score=60,
    )

    # Cast vote
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["vote_weight"] == 5
    assert data["new_vote_score"] == 5
    assert data["auto_approved"] is True  # Should auto-publish at threshold


@pytest.mark.asyncio
async def test_auto_publish_at_threshold(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that author auto-publishes when vote_score >= 5."""
    # Create pending author
    submitter_id = uuid4()
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=4,  # One vote away from threshold
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    # Create contributor JWT
    contributor_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:vote"],
        trust_score=15,
    )

    # Cast final vote
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["new_vote_score"] == 5
    assert data["auto_approved"] is True

    # Verify author is now APPROVED
    await test_db.refresh(author)
    assert author.status == ContentStatus.APPROVED
    assert author.is_public is True


@pytest.mark.asyncio
async def test_duplicate_vote_prevention(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that users cannot vote twice on the same author."""
    # Create pending author
    submitter_id = uuid4()
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    # Create contributor JWT
    contributor_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:vote"],
        trust_score=15,
    )

    # Cast first vote (should succeed)
    response1 = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert response1.status_code == 200

    # Try to vote again (should fail)
    response2 = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert response2.status_code == 400
    assert "already voted" in response2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_vote_retraction(async_client: AsyncClient, test_db: AsyncSession):
    """Test that users can retract their votes."""
    # Create pending author with one vote
    submitter_id = uuid4()
    contributor_id = uuid4()

    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=1,
    )
    test_db.add(author)
    await test_db.flush()

    # Add existing vote
    vote = JuryVote(
        user_id=contributor_id,
        entity_type="author",
        entity_id=author.id,
        vote_value=1,
    )
    test_db.add(vote)
    await test_db.flush()
    await test_db.refresh(author)

    # Create JWT for voter
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:vote"],
        trust_score=15,
    )

    # Retract vote
    response = await async_client.delete(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 204

    # Verify vote was removed
    await test_db.refresh(author)
    assert author.vote_score == 0


@pytest.mark.asyncio
async def test_curator_override_clears_votes(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that curator approval clears existing jury votes."""
    # Create pending author with votes
    submitter_id = uuid4()
    contributor_id = uuid4()

    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=3,
    )
    test_db.add(author)
    await test_db.flush()

    # Add existing votes
    vote = JuryVote(
        user_id=contributor_id,
        entity_type="author",
        entity_id=author.id,
        vote_value=1,
    )
    test_db.add(vote)
    await test_db.flush()

    # Create curator JWT
    curator_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=curator_id,
        scopes=["jury:override"],
        trust_score=85,
    )

    # Curator approves
    response = await async_client.post(
        f"/authors/{author.id}/approve",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200

    # Verify votes were cleared
    from sqlalchemy import select

    result = await test_db.execute(
        select(JuryVote).where(
            JuryVote.entity_type == "author", JuryVote.entity_id == author.id
        )
    )
    remaining_votes = result.scalars().all()
    assert len(remaining_votes) == 0


@pytest.mark.asyncio
async def test_voting_on_non_pending_author_fails(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that voting on already APPROVED author returns 400."""
    # Create APPROVED author
    submitter_id = uuid4()
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        vote_score=0,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    # Create contributor JWT
    contributor_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:vote"],
        trust_score=15,
    )

    # Try to vote (should fail)
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 400
    assert "PENDING" in response.json()["detail"]


@pytest.mark.asyncio
async def test_voting_without_jury_scope_fails(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that users without jury:vote scope cannot vote."""
    # Create pending author
    submitter_id = uuid4()
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    # Create regular user JWT (no jury:vote scope)
    user_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=user_id,
        scopes=["books:read", "reviews:create"],
        trust_score=5,
    )

    # Try to vote (should fail)
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 403
    assert "jury:vote" in response.json()["detail"]


@pytest.mark.asyncio
async def test_vote_weight_calculation_from_scopes(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that vote weight is correctly calculated from user scopes."""
    from helpers.jury import calculate_vote_weight

    # Contributor (only jury:vote)
    assert calculate_vote_weight(["jury:vote"]) == 1

    # Trusted (has jury:vote_weighted)
    assert calculate_vote_weight(["jury:vote", "jury:vote_weighted"]) == 5
    assert calculate_vote_weight(["jury:vote_weighted"]) == 5

    # No voting scopes
    assert calculate_vote_weight(["books:read"]) == 0
    assert calculate_vote_weight([]) == 0


@pytest.mark.asyncio
async def test_jury_queue_filtering_only_pending(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that jury queue only shows PENDING authors."""
    submitter_id = uuid4()

    # Create authors with different statuses
    pending_author = Author(
        name="Pending Author",
        email="pending@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    approved_author = Author(
        name="Approved Author",
        email="approved@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    rejected_author = Author(
        name="Rejected Author",
        email="rejected@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.REJECTED,
        is_public=False,
    )

    test_db.add_all([pending_author, approved_author, rejected_author])
    await test_db.flush()

    # Create contributor JWT
    contributor_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:view"],
        trust_score=15,
    )

    # Get jury queue
    response = await async_client.get(
        "/jury/authors", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    # Should include our pending author
    assert data["total"] >= 1
    author_names = [item["name"] for item in data["items"]]
    assert "Pending Author" in author_names
    # Should NOT include approved or rejected
    assert "Approved Author" not in author_names
    assert "Rejected Author" not in author_names


@pytest.mark.asyncio
async def test_vote_status_display(async_client: AsyncClient, test_db: AsyncSession):
    """Test that vote status shows score, voters, and breakdown."""
    # Create pending author with votes
    submitter_id = uuid4()
    voter1_id = uuid4()
    voter2_id = uuid4()

    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=5,  # DB constraint: 0-5, so use 5 not 6
    )
    test_db.add(author)
    await test_db.flush()

    # Add votes (only 1 and 5 are valid per constraint)
    vote1 = JuryVote(
        user_id=voter1_id, entity_type="author", entity_id=author.id, vote_value=1
    )
    # Note: Can't add another vote to reach exactly 5 with vote_value constraint (1, 5 only)
    # So we test with one vote showing system works with partial votes
    test_db.add(vote1)
    await test_db.flush()

    # Create contributor JWT
    contributor_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=contributor_id,
        scopes=["jury:view"],
        trust_score=15,
    )

    # Get vote status
    response = await async_client.get(
        f"/jury/authors/{author.id}/votes",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    # Verify response has expected structure
    assert "vote_weight" in data
    assert data["threshold"] == 5
    assert "votes_needed" in data
    assert "total_votes" in data
    assert "voters" in data
    assert data["total_votes"] >= 1  # At least our test vote
    assert len(data["voters"]) >= 1  # Voters list populated


@pytest.mark.asyncio
async def test_trust_adjustment_after_jury_approval(
    async_client: AsyncClient, test_db: AsyncSession, mocker
):
    """Test that submitter gets +10 trust when jury approves."""
    # Mock the Auth Service call
    mock_adjust_trust = mocker.patch(
        "helpers.jury.adjust_trust_for_approval", return_value=None
    )

    # Create pending author
    submitter_id = uuid4()
    author = Author(
        name="Test Author",
        email="test@example.com",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,  # Start at 0 so weighted vote (+5) reaches threshold
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)

    # Create trusted user JWT (vote weight = 5)
    trusted_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=trusted_id,
        scopes=["jury:vote_weighted"],
        trust_score=60,
    )

    # Cast weighted vote (+5) that triggers auto-publish (0+5=5, reaches threshold)
    response = await async_client.post(
        f"/jury/authors/{author.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200

    # Verify trust adjustment was called
    mock_adjust_trust.assert_called_once_with(
        user_id=submitter_id,
        entity_type="author",
        entity_id=author.id,
        is_book=False,
    )
