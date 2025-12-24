"""
Tests for upload endpoints (/uploads/*).
Tests presign and commit workflows for books, authors, and collections.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from httpx import AsyncClient

from models import Book, Author, Collection, ContentStatus
from helpers.jwt_utils import create_test_jwt


# ========================================
# FIXTURES
# ========================================


@pytest.fixture
def mock_s3_client():
    """Mock S3 client for testing presigned URL generation."""
    mock = MagicMock()
    mock.generate_presigned_post.return_value = {
        "url": "https://s3.example.com/bucket",
        "fields": {
            "key": "library/books/1/tmp/test-uuid",
            "Content-Type": "image/jpeg",
            "policy": "base64policy",
            "x-amz-signature": "signature",
        },
    }
    return mock


@pytest.fixture
def owner_user_id():
    """Generate a consistent owner user ID."""
    return uuid4()


@pytest.fixture
def owner_token(owner_user_id):
    """Token for content owner with update_own scope."""
    return create_test_jwt(
        user_id=owner_user_id,
        scopes=[
            "books:create",
            "books:update_own",
            "authors:create",
            "authors:update_own",
        ],
        trust_score=50,
    )


@pytest.fixture
def wiki_editor_token():
    """Token for wiki editor with edit_public_meta scope."""
    return create_test_jwt(
        user_id=uuid4(),
        scopes=["books:edit_public_meta", "authors:update_public_meta"],
        trust_score=80,
    )


@pytest.fixture
def no_scope_token():
    """Token with no relevant scopes."""
    return create_test_jwt(
        user_id=uuid4(),
        scopes=["users:read"],
        trust_score=30,
    )


# ========================================
# BOOK COVER UPLOAD TESTS
# ========================================


@pytest.mark.asyncio
async def test_presign_book_cover_owner_success(
    async_client: AsyncClient, test_db, owner_user_id, owner_token, mock_s3_client
):
    """Owner with books:update_own can presign cover upload."""
    from services.storage import get_s3_client
    from main import app

    # Create a book owned by the user
    book = Book(
        title="Test Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(book)
    await test_db.flush()

    # Override S3 client
    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            f"/uploads/books/{book.id}/cover",
            json={"content_type": "image/jpeg"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "upload_id" in data
        assert "s3_key" in data
        assert "url" in data
        assert "fields" in data
        assert "expires_at" in data
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


@pytest.mark.asyncio
async def test_presign_book_cover_non_owner_forbidden(
    async_client: AsyncClient, test_db, owner_user_id, mock_s3_client
):
    """Non-owner without wiki scope cannot presign cover upload."""
    from services.storage import get_s3_client
    from main import app

    # Create a book owned by a different user
    book = Book(
        title="Test Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(book)
    await test_db.flush()

    # Different user trying to upload
    other_user_token = create_test_jwt(
        user_id=uuid4(),
        scopes=["books:update_own"],
        trust_score=50,
    )

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            f"/uploads/books/{book.id}/cover",
            json={"content_type": "image/jpeg"},
            headers={"Authorization": f"Bearer {other_user_token}"},
        )

        assert response.status_code == 403
        assert "Insufficient permissions" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


@pytest.mark.asyncio
async def test_presign_book_cover_wiki_editor_approved_only(
    async_client: AsyncClient, test_db, owner_user_id, wiki_editor_token, mock_s3_client
):
    """Wiki editor can only presign for APPROVED books."""
    from services.storage import get_s3_client
    from main import app

    # Create an APPROVED book
    approved_book = Book(
        title="Approved Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.APPROVED,
        is_public=True,
        version=1,
    )
    test_db.add(approved_book)
    await test_db.flush()

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            f"/uploads/books/{approved_book.id}/cover",
            json={"content_type": "image/jpeg"},
            headers={"Authorization": f"Bearer {wiki_editor_token}"},
        )

        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


@pytest.mark.asyncio
async def test_presign_book_cover_invalid_mime_type(
    async_client: AsyncClient, test_db, owner_user_id, owner_token, mock_s3_client
):
    """Invalid MIME type should be rejected."""
    from services.storage import get_s3_client
    from main import app

    book = Book(
        title="Test Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(book)
    await test_db.flush()

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            f"/uploads/books/{book.id}/cover",
            json={"content_type": "application/pdf"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 400
        assert "Unsupported content type" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


@pytest.mark.asyncio
async def test_presign_book_cover_not_found(
    async_client: AsyncClient, owner_token, mock_s3_client
):
    """Presign for non-existent book returns 404."""
    from services.storage import get_s3_client
    from main import app

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            "/uploads/books/99999/cover",
            json={"content_type": "image/jpeg"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


# ========================================
# COMMIT TESTS
# ========================================


@pytest.mark.asyncio
async def test_commit_book_cover_version_conflict(
    async_client: AsyncClient, test_db, owner_user_id, owner_token, mock_s3_client
):
    """Commit fails if book version changed since presign."""
    from services.storage import get_s3_client
    from cache import create_upload_claim, get_redis
    from main import app
    from tests.conftest import FakeRedis

    book = Book(
        title="Test Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(book)
    await test_db.flush()

    # Create a claim with old version
    fake_redis = FakeRedis()
    upload_id = uuid4()
    s3_key = f"library/books/{book.id}/tmp/{upload_id}"

    await create_upload_claim(
        user_id=owner_user_id,
        upload_id=upload_id,
        s3_key=s3_key,
        upload_type="cover",
        entity_type="book",
        entity_id=book.id,
        entity_version=1,  # Version 1
        expected_mime="image/jpeg",
        max_bytes=10_000_000,
        expires_at_ts=int(datetime.now(timezone.utc).timestamp()) + 600,
        ttl_seconds=900,
        r=fake_redis,
    )

    # Simulate book being updated (version changed)
    book.version = 2
    await test_db.flush()

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client
    app.dependency_overrides[get_redis] = lambda: fake_redis

    try:
        response = await async_client.post(
            f"/uploads/books/{book.id}/cover/commit",
            json={"upload_id": str(upload_id), "s3_key": s3_key},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 409
        assert "modified during upload" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_redis, None)


# ========================================
# AUTHOR AVATAR UPLOAD TESTS
# ========================================


@pytest.mark.asyncio
async def test_presign_author_avatar_success(
    async_client: AsyncClient, test_db, owner_user_id, owner_token, mock_s3_client
):
    """Owner can presign avatar upload for their author."""
    from services.storage import get_s3_client
    from main import app

    author = Author(
        name="Test Author",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(author)
    await test_db.flush()

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            f"/uploads/authors/{author.id}/avatar",
            json={"content_type": "image/png"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "upload_id" in data
        assert "authors" in data["s3_key"]
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


# ========================================
# BOOK FILE UPLOAD TESTS
# ========================================


@pytest.mark.asyncio
async def test_presign_book_file_valid_format(
    async_client: AsyncClient, test_db, owner_user_id, owner_token, mock_s3_client
):
    """Owner can presign PDF/EPUB file upload."""
    from services.storage import get_s3_client
    from main import app

    book = Book(
        title="Test Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(book)
    await test_db.flush()

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            f"/uploads/books/{book.id}/file",
            json={"filename": "book.pdf", "content_type": "application/pdf"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


@pytest.mark.asyncio
async def test_presign_book_file_invalid_format(
    async_client: AsyncClient, test_db, owner_user_id, owner_token, mock_s3_client
):
    """Invalid file format should be rejected."""
    from services.storage import get_s3_client
    from main import app

    book = Book(
        title="Test Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(book)
    await test_db.flush()

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

    try:
        response = await async_client.post(
            f"/uploads/books/{book.id}/file",
            json={"filename": "book.exe", "content_type": "application/octet-stream"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 400
        assert "Unsupported file format" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_s3_client, None)


# ========================================
# RATE LIMIT TESTS
# ========================================


@pytest.mark.asyncio
async def test_upload_rate_limiting(
    async_client: AsyncClient, test_db, owner_user_id, owner_token, mock_s3_client
):
    """Excessive upload requests should be rate limited."""
    from services.storage import get_s3_client
    from cache import get_redis
    from main import app
    from tests.conftest import FakeRedis

    book = Book(
        title="Test Book",
        created_by_user_id=owner_user_id,
        status=ContentStatus.PENDING,
        version=1,
    )
    test_db.add(book)
    await test_db.flush()

    # Create a FakeRedis that tracks rate limit calls
    fake_redis = FakeRedis()
    # Pre-exhaust the rate limit
    rl_key = f"rl:uploads:book_cover:{owner_user_id}"
    fake_redis.hash_store[rl_key] = {
        "tokens": "0",
        "last_refill_ms": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
    }

    app.dependency_overrides[get_s3_client] = lambda: mock_s3_client
    app.dependency_overrides[get_redis] = lambda: fake_redis

    try:
        response = await async_client.post(
            f"/uploads/books/{book.id}/cover",
            json={"content_type": "image/jpeg"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )

        assert response.status_code == 429
        assert "Too many upload requests" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_redis, None)
