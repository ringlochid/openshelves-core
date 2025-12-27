"""
Upload router for media management.
Provides presign and commit endpoints for book covers, book files,
author avatars, and collection covers.

Pattern: presign → client uploads to S3 → commit → Celery processes

Follows the same permission patterns as author/book/collection routers:
- Owner with {entity}:update_own scope can upload
- Wiki editor with {entity}:edit_public_meta can upload to APPROVED content

Version checking: Entity version is captured at presign time and verified
at commit time to prevent lost updates if entity is modified during upload.
"""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_async_db
import cache
from cache import Redis, get_redis, token_bucket_allow, make_rate_limit_key
from cache import create_upload_claim, consume_upload_claim
from dependencies.auth import get_current_user
from models import Author, Book, Collection, ContentStatus
from schemas.upload import (
    BookCoverUploadRequest,
    BookFileUploadRequest,
    AuthorAvatarUploadRequest,
    CollectionCoverUploadRequest,
    UploadResponse,
    CommitRequest,
)
from services.storage import get_s3_client
from settings import settings
from tasks.media import process_cover, process_avatar, process_book_file


router = APIRouter(prefix="/uploads", tags=["Uploads"])


# ========================================
# BOOK COVER ENDPOINTS
# ========================================


@router.post(
    "/books/{book_id}/cover",
    response_model=UploadResponse,
    summary="Request presigned URL for book cover upload",
)
async def presign_book_cover(
    book_id: int,
    payload: BookCoverUploadRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
    s3_client=Depends(get_s3_client),
):
    """
    Get a presigned URL to upload a book cover image.
    Requires: books:update_own (owner) or books:edit_public_meta (wiki editor for APPROVED).
    """
    # Rate limit
    rl_key = make_rate_limit_key("uploads:book_cover", str(current_user["user_id"]))
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_UPLOAD_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_UPLOAD_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_UPLOAD_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many upload requests")

    # Validate MIME type
    if payload.content_type not in settings.COVER_ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content type. Allowed: {settings.COVER_ALLOWED_MIME_TYPES}",
        )

    # Load book
    query = select(Book).where(Book.id == book_id)
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.is_deleted:
        raise HTTPException(status_code=410, detail="Book has been deleted")

    # Permission check (same pattern as book router)
    user_id = current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    is_owner = book.created_by_user_id == user_id
    has_update_own = "books:update_own" in user_scopes
    is_wiki_editor = (
        "books:edit_public_meta" in user_scopes
        and book.status == ContentStatus.APPROVED
    )

    if is_owner and has_update_own:
        pass
    elif is_wiki_editor:
        pass
    else:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions. Owner needs 'books:update_own' or wiki-editor needs 'books:edit_public_meta' (APPROVED only)",
        )

    # Capture version for optimistic locking
    entity_version = book.version

    # Generate presigned upload URL
    upload_id = uuid.uuid4()
    expires_ts = (
        int(datetime.now(timezone.utc).timestamp()) + settings.UPLOAD_EXPIRES_SECONDS
    )
    s3_key = f"tmp/books/{book_id}/{upload_id}"

    try:
        presigned = s3_client.generate_presigned_post(
            Bucket=settings.S3_BUCKET_NAME,
            Key=s3_key,
            Fields={"Content-Type": payload.content_type},
            Conditions=[
                [
                    "starts-with",
                    "$Content-Type",
                    payload.content_type.split("/")[0] + "/",
                ],
                ["content-length-range", 1, settings.COVER_MAX_BYTES],
            ],
            ExpiresIn=settings.UPLOAD_EXPIRES_SECONDS,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create upload URL")

    # Store claim in Redis with version
    await create_upload_claim(
        user_id=user_id,
        upload_id=upload_id,
        s3_key=s3_key,
        upload_type="book_cover",
        entity_type="book",
        entity_id=book_id,
        entity_version=entity_version,
        expected_mime=payload.content_type,
        max_bytes=settings.COVER_MAX_BYTES,
        expires_at_ts=expires_ts,
        ttl_seconds=settings.UPLOAD_EXPIRES_SECONDS + 300,
        r=r,
    )

    return UploadResponse(
        upload_id=upload_id,
        s3_key=s3_key,
        url=presigned["url"],
        fields=presigned["fields"],
        expires_at=datetime.fromtimestamp(expires_ts, tz=timezone.utc),
    )


@router.post(
    "/books/{book_id}/cover/commit",
    status_code=204,
    summary="Commit book cover upload and start processing",
)
async def commit_book_cover(
    book_id: int,
    payload: CommitRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    """Confirm book cover upload and trigger processing."""
    # Rate limit
    rl_key = make_rate_limit_key("commits:book_cover", str(current_user["user_id"]))
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_COMMIT_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_COMMIT_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_COMMIT_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many commit requests")

    # Consume claim
    claim = await consume_upload_claim(current_user["user_id"], payload.upload_id, r)
    if not claim:
        raise HTTPException(status_code=400, detail="Upload not found or expired")
    if claim.get("key") != payload.s3_key:
        raise HTTPException(status_code=400, detail="S3 key mismatch")
    if claim.get("entity_id") != str(book_id):
        raise HTTPException(status_code=400, detail="Entity ID mismatch")
    # Prevent cross-entity claim reuse
    if claim.get("entity_type") != "book":
        raise HTTPException(
            status_code=400, detail="Invalid claim: entity type mismatch"
        )
    if claim.get("upload_type") != "book_cover":
        raise HTTPException(
            status_code=400, detail="Invalid claim: upload type mismatch"
        )

    # Version check - verify entity hasn't changed since presign
    query = select(Book).where(Book.id == book_id)
    result = await db.execute(query)
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    claimed_version = int(claim.get("entity_version", 0))
    if book.version != claimed_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Book was modified during upload (version {claimed_version} → {book.version}). Please re-upload.",
        )

    # Dispatch Celery task with version for edit history
    process_cover.delay(
        s3_key=payload.s3_key,
        entity_type="book",
        entity_id=book_id,
        entity_version=book.version,
        user_id=str(current_user["user_id"]),
    )


# ========================================
# BOOK FILE ENDPOINTS
# ========================================


@router.post(
    "/books/{book_id}/file",
    response_model=UploadResponse,
    summary="Request presigned URL for book file upload",
)
async def presign_book_file(
    book_id: int,
    payload: BookFileUploadRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
    s3_client=Depends(get_s3_client),
):
    """
    Get a presigned URL to upload a book file (PDF/EPUB).
    Requires: books:update_own (owner) or books:edit_public_meta (wiki editor for APPROVED).
    """
    rl_key = make_rate_limit_key("uploads:book_file", str(current_user["user_id"]))
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_UPLOAD_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_UPLOAD_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_UPLOAD_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many upload requests")

    # Validate file format
    ext = payload.filename.rsplit(".", 1)[-1].lower() if "." in payload.filename else ""
    if ext not in settings.BOOK_ALLOWED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Allowed: {settings.BOOK_ALLOWED_FORMATS}",
        )

    query = select(Book).where(Book.id == book_id)
    result = await db.execute(query)
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.is_deleted:
        raise HTTPException(status_code=410, detail="Book has been deleted")

    user_id = current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    is_owner = book.created_by_user_id == user_id
    has_update_own = "books:update_own" in user_scopes
    is_wiki_editor = (
        "books:edit_public_meta" in user_scopes
        and book.status == ContentStatus.APPROVED
    )

    if is_owner and has_update_own:
        pass
    elif is_wiki_editor:
        pass
    else:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions.",
        )

    entity_version = book.version
    upload_id = uuid.uuid4()
    expires_ts = (
        int(datetime.now(timezone.utc).timestamp()) + settings.UPLOAD_EXPIRES_SECONDS
    )
    s3_key = f"tmp/books/{book_id}/{upload_id}"

    try:
        presigned = s3_client.generate_presigned_post(
            Bucket=settings.S3_BUCKET_NAME,
            Key=s3_key,
            Fields={"Content-Type": payload.content_type},
            Conditions=[
                {"Content-Type": payload.content_type},
                ["content-length-range", 1, settings.BOOK_FILE_MAX_BYTES],
            ],
            ExpiresIn=settings.UPLOAD_EXPIRES_SECONDS,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create upload URL")

    await create_upload_claim(
        user_id=user_id,
        upload_id=upload_id,
        s3_key=s3_key,
        upload_type="book_file",
        entity_type="book",
        entity_id=book_id,
        entity_version=entity_version,
        expected_mime=payload.content_type,
        max_bytes=settings.BOOK_FILE_MAX_BYTES,
        expires_at_ts=expires_ts,
        ttl_seconds=settings.UPLOAD_EXPIRES_SECONDS + 300,
        r=r,
    )

    return UploadResponse(
        upload_id=upload_id,
        s3_key=s3_key,
        url=presigned["url"],
        fields=presigned["fields"],
        expires_at=datetime.fromtimestamp(expires_ts, tz=timezone.utc),
    )


@router.post(
    "/books/{book_id}/file/commit",
    status_code=204,
    summary="Commit book file upload and start processing",
)
async def commit_book_file(
    book_id: int,
    payload: CommitRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    """Confirm book file upload and trigger processing."""
    rl_key = make_rate_limit_key("commits:book_file", str(current_user["user_id"]))
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_COMMIT_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_COMMIT_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_COMMIT_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many commit requests")

    claim = await consume_upload_claim(current_user["user_id"], payload.upload_id, r)
    if not claim:
        raise HTTPException(status_code=400, detail="Upload not found or expired")
    if claim.get("key") != payload.s3_key:
        raise HTTPException(status_code=400, detail="S3 key mismatch")
    if claim.get("entity_id") != str(book_id):
        raise HTTPException(status_code=400, detail="Entity ID mismatch")
    # Prevent cross-entity claim reuse
    if claim.get("entity_type") != "book":
        raise HTTPException(
            status_code=400, detail="Invalid claim: entity type mismatch"
        )
    if claim.get("upload_type") != "book_file":
        raise HTTPException(
            status_code=400, detail="Invalid claim: upload type mismatch"
        )

    query = select(Book).where(Book.id == book_id)
    result = await db.execute(query)
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    claimed_version = int(claim.get("entity_version", 0))
    if book.version != claimed_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Book was modified during upload. Please re-upload.",
        )

    process_book_file.delay(
        s3_key=payload.s3_key,
        book_id=book_id,
        entity_version=book.version,
        user_id=str(current_user["user_id"]),
    )


# ========================================
# AUTHOR AVATAR ENDPOINTS
# ========================================


@router.post(
    "/authors/{author_id}/avatar",
    response_model=UploadResponse,
    summary="Request presigned URL for author avatar upload",
)
async def presign_author_avatar(
    author_id: int,
    payload: AuthorAvatarUploadRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
    s3_client=Depends(get_s3_client),
):
    """
    Get a presigned URL to upload an author avatar image.
    Requires: authors:update_own (owner) or authors:update_public_meta (wiki editor for APPROVED).
    """
    rl_key = make_rate_limit_key("uploads:author_avatar", str(current_user["user_id"]))
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_UPLOAD_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_UPLOAD_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_UPLOAD_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many upload requests")

    if payload.content_type not in settings.COVER_ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content type. Allowed: {settings.COVER_ALLOWED_MIME_TYPES}",
        )

    query = select(Author).where(Author.id == author_id)
    result = await db.execute(query)
    author = result.scalar_one_or_none()

    if not author:
        raise HTTPException(status_code=404, detail="Author not found")
    if author.is_deleted:
        raise HTTPException(status_code=410, detail="Author has been deleted")

    user_id = current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    is_owner = author.created_by_user_id == user_id
    has_update_own = "authors:update_own" in user_scopes
    has_update_public_meta = (
        "authors:update_public_meta" in user_scopes
        and author.status == ContentStatus.APPROVED
    )

    if is_owner and has_update_own:
        pass
    elif has_update_public_meta:
        pass
    else:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions.",
        )

    entity_version = author.version
    upload_id = uuid.uuid4()
    expires_ts = (
        int(datetime.now(timezone.utc).timestamp()) + settings.UPLOAD_EXPIRES_SECONDS
    )
    s3_key = f"tmp/authors/{author_id}/{upload_id}"

    try:
        presigned = s3_client.generate_presigned_post(
            Bucket=settings.S3_BUCKET_NAME,
            Key=s3_key,
            Fields={"Content-Type": payload.content_type},
            Conditions=[
                [
                    "starts-with",
                    "$Content-Type",
                    payload.content_type.split("/")[0] + "/",
                ],
                ["content-length-range", 1, settings.COVER_MAX_BYTES],
            ],
            ExpiresIn=settings.UPLOAD_EXPIRES_SECONDS,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create upload URL")

    await create_upload_claim(
        user_id=user_id,
        upload_id=upload_id,
        s3_key=s3_key,
        upload_type="author_avatar",
        entity_type="author",
        entity_id=author_id,
        entity_version=entity_version,
        expected_mime=payload.content_type,
        max_bytes=settings.COVER_MAX_BYTES,
        expires_at_ts=expires_ts,
        ttl_seconds=settings.UPLOAD_EXPIRES_SECONDS + 300,
        r=r,
    )

    return UploadResponse(
        upload_id=upload_id,
        s3_key=s3_key,
        url=presigned["url"],
        fields=presigned["fields"],
        expires_at=datetime.fromtimestamp(expires_ts, tz=timezone.utc),
    )


@router.post(
    "/authors/{author_id}/avatar/commit",
    status_code=204,
    summary="Commit author avatar upload and start processing",
)
async def commit_author_avatar(
    author_id: int,
    payload: CommitRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    """Confirm author avatar upload and trigger processing."""
    rl_key = make_rate_limit_key("commits:author_avatar", str(current_user["user_id"]))
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_COMMIT_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_COMMIT_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_COMMIT_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many commit requests")

    claim = await consume_upload_claim(current_user["user_id"], payload.upload_id, r)
    if not claim:
        raise HTTPException(status_code=400, detail="Upload not found or expired")
    if claim.get("key") != payload.s3_key:
        raise HTTPException(status_code=400, detail="S3 key mismatch")
    if claim.get("entity_id") != str(author_id):
        raise HTTPException(status_code=400, detail="Entity ID mismatch")
    # Prevent cross-entity claim reuse
    if claim.get("entity_type") != "author":
        raise HTTPException(
            status_code=400, detail="Invalid claim: entity type mismatch"
        )
    if claim.get("upload_type") != "author_avatar":
        raise HTTPException(
            status_code=400, detail="Invalid claim: upload type mismatch"
        )

    query = select(Author).where(Author.id == author_id)
    result = await db.execute(query)
    author = result.scalar_one_or_none()
    if not author:
        raise HTTPException(status_code=404, detail="Author not found")

    claimed_version = int(claim.get("entity_version", 0))
    if author.version != claimed_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Author was modified during upload. Please re-upload.",
        )

    # Use separate avatar task for square sizes
    process_avatar.delay(
        s3_key=payload.s3_key,
        entity_type="author",
        entity_id=author_id,
        entity_version=author.version,
        user_id=str(current_user["user_id"]),
    )


# ========================================
# COLLECTION COVER ENDPOINTS
# ========================================


@router.post(
    "/collections/{collection_id}/cover",
    response_model=UploadResponse,
    summary="Request presigned URL for collection cover upload",
)
async def presign_collection_cover(
    collection_id: int,
    payload: CollectionCoverUploadRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
    s3_client=Depends(get_s3_client),
):
    """
    Get a presigned URL to upload a collection cover image.
    Requires: collections:update_own (owner) or collections:update_public_meta (wiki editor for APPROVED).
    """
    rl_key = make_rate_limit_key(
        "uploads:collection_cover", str(current_user["user_id"])
    )
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_UPLOAD_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_UPLOAD_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_UPLOAD_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many upload requests")

    if payload.content_type not in settings.COVER_ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content type. Allowed: {settings.COVER_ALLOWED_MIME_TYPES}",
        )

    query = select(Collection).where(Collection.id == collection_id)
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.is_deleted:
        raise HTTPException(status_code=410, detail="Collection has been deleted")

    user_id = current_user["user_id"]
    user_scopes = current_user.get("scopes", [])
    is_owner = collection.created_by_user_id == user_id
    has_update_own = "collections:update_own" in user_scopes
    has_update_public_meta = (
        "collections:update_public_meta" in user_scopes
        and collection.status == ContentStatus.APPROVED
    )

    if is_owner and has_update_own:
        pass
    elif has_update_public_meta:
        pass
    else:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions.",
        )

    entity_version = collection.version
    upload_id = uuid.uuid4()
    expires_ts = (
        int(datetime.now(timezone.utc).timestamp()) + settings.UPLOAD_EXPIRES_SECONDS
    )
    s3_key = f"tmp/collections/{collection_id}/{upload_id}"

    try:
        presigned = s3_client.generate_presigned_post(
            Bucket=settings.S3_BUCKET_NAME,
            Key=s3_key,
            Fields={"Content-Type": payload.content_type},
            Conditions=[
                [
                    "starts-with",
                    "$Content-Type",
                    payload.content_type.split("/")[0] + "/",
                ],
                ["content-length-range", 1, settings.COVER_MAX_BYTES],
            ],
            ExpiresIn=settings.UPLOAD_EXPIRES_SECONDS,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create upload URL")

    await create_upload_claim(
        user_id=user_id,
        upload_id=upload_id,
        s3_key=s3_key,
        upload_type="collection_cover",
        entity_type="collection",
        entity_id=collection_id,
        entity_version=entity_version,
        expected_mime=payload.content_type,
        max_bytes=settings.COVER_MAX_BYTES,
        expires_at_ts=expires_ts,
        ttl_seconds=settings.UPLOAD_EXPIRES_SECONDS + 300,
        r=r,
    )

    return UploadResponse(
        upload_id=upload_id,
        s3_key=s3_key,
        url=presigned["url"],
        fields=presigned["fields"],
        expires_at=datetime.fromtimestamp(expires_ts, tz=timezone.utc),
    )


@router.post(
    "/collections/{collection_id}/cover/commit",
    status_code=204,
    summary="Commit collection cover upload and start processing",
)
async def commit_collection_cover(
    collection_id: int,
    payload: CommitRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    r: Redis = Depends(get_redis),
):
    """Confirm collection cover upload and trigger processing."""
    rl_key = make_rate_limit_key(
        "commits:collection_cover", str(current_user["user_id"])
    )
    allowed, _ = await token_bucket_allow(
        rl_key,
        capacity=settings.RATE_LIMIT_COMMIT_CAPACITY,
        refill_tokens=settings.RATE_LIMIT_COMMIT_REFILL_TOKENS,
        refill_period_seconds=settings.RATE_LIMIT_COMMIT_REFILL_PERIOD_SECONDS,
        r=r,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many commit requests")

    claim = await consume_upload_claim(current_user["user_id"], payload.upload_id, r)
    if not claim:
        raise HTTPException(status_code=400, detail="Upload not found or expired")
    if claim.get("key") != payload.s3_key:
        raise HTTPException(status_code=400, detail="S3 key mismatch")
    if claim.get("entity_id") != str(collection_id):
        raise HTTPException(status_code=400, detail="Entity ID mismatch")
    # Prevent cross-entity claim reuse
    if claim.get("entity_type") != "collection":
        raise HTTPException(
            status_code=400, detail="Invalid claim: entity type mismatch"
        )
    if claim.get("upload_type") != "collection_cover":
        raise HTTPException(
            status_code=400, detail="Invalid claim: upload type mismatch"
        )

    query = select(Collection).where(Collection.id == collection_id)
    result = await db.execute(query)
    collection = result.scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    claimed_version = int(claim.get("entity_version", 0))
    if collection.version != claimed_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Collection was modified during upload. Please re-upload.",
        )

    process_cover.delay(
        s3_key=payload.s3_key,
        entity_type="collection",
        entity_id=collection_id,
        entity_version=collection.version,
        user_id=str(current_user["user_id"]),
    )
