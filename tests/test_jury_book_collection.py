"""
Tests for jury voting flows on books and collections.
"""

import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Book, Collection, JuryVote, ContentStatus
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_jury_collection_queue_shows_pending(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that jury queue shows only PENDING collections."""
    user_id = uuid4()

    pending_collection = Collection(
        name="Pending Collection Jury",
        created_by_user_id=user_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    approved_collection = Collection(
        name="Approved Collection Jury",
        created_by_user_id=user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
    )

    test_db.add_all([pending_collection, approved_collection])
    await test_db.flush()

    jwt_token = create_test_jwt(user_id=uuid4(), scopes=["jury:view"], trust_score=15)

    response = await async_client.get(
        "/jury/collections", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    names = [c["name"] for c in data["items"]]
    assert "Pending Collection Jury" in names
    assert "Approved Collection Jury" not in names


@pytest.mark.asyncio
async def test_jury_vote_on_book(async_client: AsyncClient, test_db: AsyncSession):
    """Test voting on a pending book."""
    submitter_id = uuid4()
    voter_id = uuid4()

    book = Book(
        title="Vote Test Book Jury",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(book)
    await test_db.flush()
    await test_db.refresh(book)

    jwt_token = create_test_jwt(user_id=voter_id, scopes=["jury:vote"], trust_score=15)

    response = await async_client.post(
        f"/jury/books/{book.id}/vote", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    assert data["vote_weight"] == 1
    assert data["new_vote_score"] == 1


@pytest.mark.asyncio
async def test_jury_vote_on_collection(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test voting on a pending collection."""
    submitter_id = uuid4()
    voter_id = uuid4()

    collection = Collection(
        name="Vote Test Collection Jury",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=0,
    )
    test_db.add(collection)
    await test_db.flush()
    await test_db.refresh(collection)

    jwt_token = create_test_jwt(user_id=voter_id, scopes=["jury:vote"], trust_score=15)

    response = await async_client.post(
        f"/jury/collections/{collection.id}/vote",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["vote_weight"] == 1
    assert data["new_vote_score"] == 1


@pytest.mark.asyncio
async def test_jury_book_auto_publish_at_threshold(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that book auto-publishes when vote score reaches threshold."""
    submitter_id = uuid4()
    voter_id = uuid4()

    book = Book(
        title="Almost Approved Book Jury",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=4,
    )
    test_db.add(book)
    await test_db.flush()
    await test_db.refresh(book)

    jwt_token = create_test_jwt(user_id=voter_id, scopes=["jury:vote"], trust_score=15)

    response = await async_client.post(
        f"/jury/books/{book.id}/vote", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 200
    data = response.json()

    assert data["new_vote_score"] == 5
    assert data["auto_approved"] is True

    await test_db.refresh(book)
    assert book.status == ContentStatus.APPROVED


@pytest.mark.asyncio
async def test_jury_retract_vote_on_book(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test retracting a vote on a book."""
    submitter_id = uuid4()
    voter_id = uuid4()

    book = Book(
        title="Retract Test Book Jury",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
        vote_score=1,
    )
    test_db.add(book)
    await test_db.flush()

    vote = JuryVote(
        user_id=voter_id,
        entity_type="book",
        entity_id=book.id,
        vote_value=1,
    )
    test_db.add(vote)
    await test_db.flush()
    await test_db.refresh(book)

    jwt_token = create_test_jwt(user_id=voter_id, scopes=["jury:vote"], trust_score=15)

    response = await async_client.delete(
        f"/jury/books/{book.id}/vote", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 204

    await test_db.refresh(book)
    assert book.vote_score == 0


@pytest.mark.asyncio
async def test_jury_vote_requires_scope(
    async_client: AsyncClient, test_db: AsyncSession
):
    """Test that voting requires jury:vote scope."""
    submitter_id = uuid4()
    voter_id = uuid4()

    book = Book(
        title="Scope Test Book Jury",
        created_by_user_id=submitter_id,
        status=ContentStatus.PENDING,
        is_public=False,
    )
    test_db.add(book)
    await test_db.flush()
    await test_db.refresh(book)

    jwt_token = create_test_jwt(user_id=voter_id, scopes=["books:read"], trust_score=15)

    response = await async_client.post(
        f"/jury/books/{book.id}/vote", headers={"Authorization": f"Bearer {jwt_token}"}
    )

    assert response.status_code == 403
