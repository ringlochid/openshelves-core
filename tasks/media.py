"""
Media-related Celery tasks: validation, resize, virus scan, S3 moves.
Implements edit history recording and cache invalidation.
"""

import asyncio
import io
import math
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import boto3
from PIL import Image, ImageOps, UnidentifiedImageError
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from helpers.edit_history import record_update, serialize_entity
from celery_app import app
from database import create_worker_session
from models import Author, Book, Collection, EditHistory, EditAction
from cache import (
    create_worker_redis,
    invalidate_author,
    invalidate_book,
    invalidate_collection,
)
from settings import settings

try:
    import clamd  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    clamd = None

# Guard against image bombs; Pillow will raise if exceeded.
Image.MAX_IMAGE_PIXELS = settings.COVER_MAX_PIXELS


class MediaProcessingError(Exception):
    """Raised when a media upload fails validation or processing."""


# ========================================
# HELPER FUNCTIONS
# ========================================


def _get_s3_client():
    """Get S3 client - supports both explicit credentials (local dev) and IAM roles (AWS)."""
    client_kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        client_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    if settings.S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
    return boto3.client("s3", **client_kwargs)


# MIME type to file extension mapping (use "jpeg" not "jpg" for consistency)
MIME_TO_EXT: dict[str, set[str]] = {
    "image/jpeg": {"jpg", "jpeg"},
    "image/png": {"png"},
    "image/webp": {"webp"},
    "image/avif": {"avif"},
}


def _allowed_extensions() -> set[str]:
    """
    Derive allowed filename extensions from allowed MIME types.
    """
    allowed_exts: set[str] = set()
    for mime in settings.COVER_ALLOWED_MIME_TYPES:
        allowed_exts.update(MIME_TO_EXT.get(mime, set()))
    return allowed_exts


def _clamd_client():
    """Get ClamAV client if configured (CLAMAV_HOST is set and not empty)."""
    if not settings.CLAMAV_HOST:
        return None
    if clamd is None:
        raise MediaProcessingError(
            "CLAMAV_HOST is set but the clamd package is not installed"
        )
    return clamd.ClamdNetworkSocket(
        host=settings.CLAMAV_HOST, port=settings.CLAMAV_PORT
    )


def _av_scan(file_bytes: bytes) -> None:
    """Perform AV scan via clamd if configured."""
    client = _clamd_client()
    if client is None:
        return
    try:
        result = client.instream(io.BytesIO(file_bytes))
    except Exception as exc:
        raise MediaProcessingError(f"AV scan failed: {exc}") from exc
    status = result.get("stream", ("ERROR", "No result from scanner"))
    if status[0] != "OK":
        raise MediaProcessingError(f"AV scan blocked file: {status}")


def _validate_pdf(file_path: str) -> bool:
    """Validate PDF file by checking magic bytes."""
    with open(file_path, "rb") as f:
        header = f.read(5)
    return header == b"%PDF-"


def _validate_epub(file_path: str) -> bool:
    """Validate EPUB file by checking ZIP structure."""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "META-INF/container.xml" not in zf.namelist():
                return False
            if "mimetype" in zf.namelist():
                mimetype = zf.read("mimetype").decode("utf-8").strip()
                return mimetype == "application/epub+zip"
            return True
    except zipfile.BadZipFile:
        return False


def _transform_image_avatar(
    file_path: str, header_content_type: str | None
) -> dict[int, dict[str, Any]]:
    """Process avatar: resize to square sizes (512, 256, 128)."""
    allowed = set(settings.COVER_ALLOWED_MIME_TYPES)
    output_format = settings.COVER_OUTPUT_FORMAT.upper()
    target_sizes = sorted(settings.AVATAR_SIZES, reverse=True)

    try:
        with Image.open(file_path) as img:
            img = ImageOps.exif_transpose(img)
            mime_from_image = Image.MIME.get(img.format or "", "")
            effective_content_type = header_content_type or mime_from_image

            if effective_content_type not in allowed and mime_from_image in allowed:
                effective_content_type = mime_from_image
            if effective_content_type not in allowed:
                raise MediaProcessingError("Unsupported image type")

            width, height = img.size
            if width * height > settings.COVER_MAX_PIXELS:
                raise MediaProcessingError("Image has too many pixels")

            has_alpha = "A" in img.getbands()
            variants: dict[int, dict[str, Any]] = {}

            for target in target_sizes:
                scale = max(target / width, target / height)
                if settings.COVER_UPSCALE_MAX:
                    scale = min(scale, settings.COVER_UPSCALE_MAX)
                new_w = max(1, math.ceil(width * scale))
                new_h = max(1, math.ceil(height * scale))
                resized = img.resize((new_w, new_h), Image.LANCZOS)

                if resized.width >= target and resized.height >= target:
                    left = (resized.width - target) // 2
                    top = (resized.height - target) // 2
                    resized = resized.crop((left, top, left + target, top + target))

                if output_format == "JPEG":
                    resized = resized.convert("RGB")
                elif output_format in {"PNG", "WEBP"} and resized.mode not in {
                    "RGB",
                    "RGBA",
                }:
                    resized = resized.convert("RGBA" if has_alpha else "RGB")

                buffer = io.BytesIO()
                save_kwargs: dict[str, Any] = {}
                if output_format in {"JPEG", "WEBP"}:
                    save_kwargs["quality"] = settings.COVER_JPEG_QUALITY
                if output_format == "WEBP":
                    save_kwargs["method"] = 6
                resized.save(buffer, format=output_format, **save_kwargs)
                buffer.seek(0)

                out_content_type = f"image/{output_format.lower()}"
                variants[target] = {
                    "bytes": buffer.getvalue(),
                    "content_type": out_content_type,
                    "width": resized.width,
                    "height": resized.height,
                }

            return variants
    except UnidentifiedImageError as exc:
        raise MediaProcessingError("File is not a valid image") from exc


def _transform_image_cover(
    file_path: str, header_content_type: str | None
) -> dict[tuple[int, int], dict[str, Any]]:
    """Process cover: resize to portrait sizes (2:3 ratio)."""
    allowed = set(settings.COVER_ALLOWED_MIME_TYPES)
    output_format = settings.COVER_OUTPUT_FORMAT.upper()
    target_sizes = sorted(settings.COVER_SIZES, key=lambda x: x[0], reverse=True)

    try:
        with Image.open(file_path) as img:
            img = ImageOps.exif_transpose(img)
            mime_from_image = Image.MIME.get(img.format or "", "")
            effective_content_type = header_content_type or mime_from_image

            if effective_content_type not in allowed and mime_from_image in allowed:
                effective_content_type = mime_from_image
            if effective_content_type not in allowed:
                raise MediaProcessingError("Unsupported image type")

            width, height = img.size
            if width * height > settings.COVER_MAX_PIXELS:
                raise MediaProcessingError("Image has too many pixels")

            has_alpha = "A" in img.getbands()
            variants: dict[tuple[int, int], dict[str, Any]] = {}

            for target_w, target_h in target_sizes:
                scale = max(target_w / width, target_h / height)
                if settings.COVER_UPSCALE_MAX:
                    scale = min(scale, settings.COVER_UPSCALE_MAX)
                new_w = max(1, math.ceil(width * scale))
                new_h = max(1, math.ceil(height * scale))
                resized = img.resize((new_w, new_h), Image.LANCZOS)

                if resized.width >= target_w and resized.height >= target_h:
                    left = (resized.width - target_w) // 2
                    top = (resized.height - target_h) // 2
                    resized = resized.crop((left, top, left + target_w, top + target_h))

                if output_format == "JPEG":
                    resized = resized.convert("RGB")
                elif output_format in {"PNG", "WEBP"} and resized.mode not in {
                    "RGB",
                    "RGBA",
                }:
                    resized = resized.convert("RGBA" if has_alpha else "RGB")

                buffer = io.BytesIO()
                save_kwargs: dict[str, Any] = {}
                if output_format in {"JPEG", "WEBP"}:
                    save_kwargs["quality"] = settings.COVER_JPEG_QUALITY
                if output_format == "WEBP":
                    save_kwargs["method"] = 6
                resized.save(buffer, format=output_format, **save_kwargs)
                buffer.seek(0)

                out_content_type = f"image/{output_format.lower()}"
                variants[(target_w, target_h)] = {
                    "bytes": buffer.getvalue(),
                    "content_type": out_content_type,
                    "width": resized.width,
                    "height": resized.height,
                }

            return variants
    except UnidentifiedImageError as exc:
        raise MediaProcessingError("File is not a valid image") from exc


def _serialize_entity(entity: Any) -> dict[str, Any]:
    """Serialize entity for edit history - delegates to helpers.edit_history.serialize_entity."""

    return serialize_entity(entity)


# ========================================
# ASYNC DB/CACHE HELPERS
# ========================================


async def _update_image_key_with_history(
    entity_type: str,
    entity_id: int,
    new_key: str,
    user_id: str,
    expected_version: int,
) -> str | None:
    """Update entity's image key (avatar_key or cover_key) with edit history and cache invalidation.

    Uses existing record_update helper from helpers/edit_history.py to avoid code duplication.
    Raises MediaProcessingError if entity version has advanced beyond expected_version.
    """
    WorkerSession, engine = create_worker_session()
    redis = create_worker_redis()

    try:
        async with WorkerSession() as session:
            # Get entity based on type - eagerly load relationships for serialization
            if entity_type == "author":
                stmt = (
                    select(Author)
                    .where(Author.id == entity_id)
                    .options(selectinload(Author.books))
                )
                result = await session.execute(stmt)
                entity = result.scalar_one_or_none()
                field_name = "avatar_key"
            elif entity_type == "book":
                stmt = (
                    select(Book)
                    .where(Book.id == entity_id)
                    .options(selectinload(Book.authors))
                )
                result = await session.execute(stmt)
                entity = result.scalar_one_or_none()
                field_name = "cover_key"
            elif entity_type == "collection":
                stmt = (
                    select(Collection)
                    .where(Collection.id == entity_id)
                    .options(selectinload(Collection.books))
                )
                result = await session.execute(stmt)
                entity = result.scalar_one_or_none()
                field_name = "cover_key"
            else:
                raise MediaProcessingError(f"Unknown entity type: {entity_type}")

            if not entity:
                return None

            # Version check - reject if entity was modified since commit
            if entity.version != expected_version:
                raise MediaProcessingError(
                    f"{entity_type.capitalize()} {entity_id} was modified during processing "
                    f"(expected version {expected_version}, found {entity.version})"
                )

            # Capture old state
            old_data = serialize_entity(entity)
            old_key = getattr(entity, field_name)

            # Apply changes
            setattr(entity, field_name, new_key)
            entity.version += 1
            entity.last_edited_by = UUID(user_id)
            entity.last_edited_at = datetime.now(timezone.utc)

            # Capture new state
            new_data = serialize_entity(entity)

            # Record edit history using existing helper
            await record_update(
                db=session,
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=UUID(user_id),
                old_data=old_data,
                new_data=new_data,
                new_version=entity.version,
                old_version=expected_version,
            )

            await session.commit()

            # Cache invalidation
            if entity_type == "author":
                await invalidate_author(entity_id, redis)
            elif entity_type == "book":
                await invalidate_book(entity_id, redis)
            elif entity_type == "collection":
                await invalidate_collection(entity_id, redis)

            return str(old_key) if old_key else None
    finally:
        await redis.aclose()
        await engine.dispose()


async def _update_book_file_with_history(
    book_id: int,
    new_file_key: str,
    file_format: str,
    user_id: str,
    expected_version: int,
) -> str | None:
    """Update book file with edit history and cache invalidation.

    Uses existing record_update helper from helpers/edit_history.py to avoid code duplication.
    Raises MediaProcessingError if entity version has advanced beyond expected_version.
    """

    WorkerSession, engine = create_worker_session()
    redis = create_worker_redis()

    try:
        async with WorkerSession() as session:
            # Eagerly load authors relationship for serialize_entity
            stmt = (
                select(Book)
                .where(Book.id == book_id)
                .options(selectinload(Book.authors))
            )
            result = await session.execute(stmt)
            book = result.scalar_one_or_none()
            if not book:
                return None

            # Version check - reject if entity was modified since commit
            if book.version != expected_version:
                raise MediaProcessingError(
                    f"Book {book_id} was modified during processing "
                    f"(expected version {expected_version}, found {book.version})"
                )

            # Capture old state
            old_data = serialize_entity(book)
            old_key = book.file_key

            # Apply changes
            book.file_key = new_file_key
            book.file_format = file_format
            book.version += 1
            book.last_edited_by = UUID(user_id)
            book.last_edited_at = datetime.now(timezone.utc)

            # Capture new state
            new_data = serialize_entity(book)

            # Record edit history using existing helper
            await record_update(
                db=session,
                entity_type="book",
                entity_id=book_id,
                user_id=UUID(user_id),
                old_data=old_data,
                new_data=new_data,
                new_version=book.version,
                old_version=expected_version,
            )

            await session.commit()
            await invalidate_book(book_id, redis)
            return str(old_key) if old_key else None
    finally:
        await redis.aclose()
        await engine.dispose()


# ========================================
# CELERY TASKS
# ========================================


@app.task(name="tasks.media.process_avatar")
def process_avatar(
    s3_key: str,
    entity_type: str,
    entity_id: int,
    entity_version: int,
    user_id: str,
) -> dict[str, Any]:
    """Process avatar image (square sizes: 512, 256, 128)."""
    bucket = settings.S3_BUCKET_NAME
    if not bucket:
        raise MediaProcessingError("S3 bucket not configured")

    s3 = _get_s3_client()

    try:
        meta = s3.head_object(Bucket=bucket, Key=s3_key)
    except ClientError as exc:
        raise MediaProcessingError("Upload not found in S3") from exc

    size = meta.get("ContentLength") or 0
    if size < 1 or size > settings.COVER_MAX_BYTES:
        raise MediaProcessingError("File size out of range")
    header_content_type = meta.get("ContentType")

    with tempfile.NamedTemporaryFile(suffix=".upload") as tmp:
        s3.download_fileobj(bucket, s3_key, tmp)
        tmp.flush()
        tmp.seek(0)
        file_bytes = tmp.read()
        _av_scan(file_bytes)
        variants = _transform_image_avatar(tmp.name, header_content_type)

    output_format = settings.COVER_OUTPUT_FORMAT.upper()
    ext = "jpg" if output_format == "JPEG" else output_format.lower()
    base_prefix = f"library/{entity_type}s/{entity_id}/{uuid.uuid4()}"

    variant_info: dict[str, dict[str, Any]] = {}
    for size_px, data in variants.items():
        variant_key = f"{base_prefix}/{size_px}.{ext}"
        s3.put_object(
            Bucket=bucket,
            Key=variant_key,
            Body=data["bytes"],
            ContentType=data["content_type"],
            Metadata={"source": "processed", "variant": str(size_px)},
        )
        variant_info[str(size_px)] = {"key": variant_key}

    primary_size = max(variants.keys())
    primary_key = variant_info[str(primary_size)]["key"]

    # Update DB with edit history and invalidate cache
    asyncio.run(
        _update_image_key_with_history(
            entity_type, entity_id, primary_key, user_id, entity_version
        )
    )

    # Clean up temp upload (keep old files for rollback)
    try:
        s3.delete_object(Bucket=bucket, Key=s3_key)
    except Exception:
        pass

    return {"status": "ok", "final_key": primary_key, "variants": variant_info}


@app.task(name="tasks.media.process_cover")
def process_cover(
    s3_key: str,
    entity_type: str,
    entity_id: int,
    entity_version: int,
    user_id: str,
) -> dict[str, Any]:
    """Process cover image (portrait sizes: 1800x2700, 1200x1800, 600x900)."""
    bucket = settings.S3_BUCKET_NAME
    if not bucket:
        raise MediaProcessingError("S3 bucket not configured")

    s3 = _get_s3_client()

    try:
        meta = s3.head_object(Bucket=bucket, Key=s3_key)
    except ClientError as exc:
        raise MediaProcessingError("Upload not found in S3") from exc

    size = meta.get("ContentLength") or 0
    if size < 1 or size > settings.COVER_MAX_BYTES:
        raise MediaProcessingError("File size out of range")
    header_content_type = meta.get("ContentType")

    with tempfile.NamedTemporaryFile(suffix=".upload") as tmp:
        s3.download_fileobj(bucket, s3_key, tmp)
        tmp.flush()
        tmp.seek(0)
        file_bytes = tmp.read()
        _av_scan(file_bytes)
        variants = _transform_image_cover(tmp.name, header_content_type)

    output_format = settings.COVER_OUTPUT_FORMAT.upper()
    ext = "jpg" if output_format == "JPEG" else output_format.lower()
    base_prefix = f"library/{entity_type}s/{entity_id}/{uuid.uuid4()}"

    variant_info: dict[str, dict[str, Any]] = {}
    for (w, h), data in variants.items():
        variant_key = f"{base_prefix}/{w}x{h}.{ext}"
        s3.put_object(
            Bucket=bucket,
            Key=variant_key,
            Body=data["bytes"],
            ContentType=data["content_type"],
            Metadata={"source": "processed", "variant": f"{w}x{h}"},
        )
        variant_info[f"{w}x{h}"] = {"key": variant_key}

    # Use largest as primary
    primary_size = max(variants.keys(), key=lambda x: x[0])
    primary_key = variant_info[f"{primary_size[0]}x{primary_size[1]}"]["key"]

    # Update DB with edit history and invalidate cache
    asyncio.run(
        _update_image_key_with_history(
            entity_type, entity_id, primary_key, user_id, entity_version
        )
    )

    # Clean up temp upload (keep old cover files for rollback)
    try:
        s3.delete_object(Bucket=bucket, Key=s3_key)
    except Exception:
        pass

    return {"status": "ok", "final_key": primary_key, "variants": variant_info}


@app.task(name="tasks.media.process_book_file")
def process_book_file(
    s3_key: str,
    book_id: int,
    entity_version: int,
    user_id: str,
) -> dict[str, Any]:
    """Process book file (PDF/EPUB): validate, scan, move to final location."""
    bucket = settings.S3_BUCKET_NAME
    if not bucket:
        raise MediaProcessingError("S3 bucket not configured")

    s3 = _get_s3_client()

    try:
        meta = s3.head_object(Bucket=bucket, Key=s3_key)
    except ClientError as exc:
        raise MediaProcessingError("Upload not found in S3") from exc

    size = meta.get("ContentLength") or 0
    if size < 1 or size > settings.BOOK_FILE_MAX_BYTES:
        raise MediaProcessingError("File size out of range")

    with tempfile.NamedTemporaryFile(suffix=".upload", delete=False) as tmp:
        s3.download_fileobj(bucket, s3_key, tmp)
        tmp.flush()
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            file_bytes = f.read()
        _av_scan(file_bytes)

        if _validate_pdf(tmp_path):
            file_format = "pdf"
        elif _validate_epub(tmp_path):
            file_format = "epub"
        else:
            raise MediaProcessingError(
                "Invalid file format. Only PDF and EPUB are supported."
            )

        # Use UUID for versioned file storage
        final_key = f"library/books/{book_id}/{uuid.uuid4()}.{file_format}"
        s3.put_object(
            Bucket=bucket,
            Key=final_key,
            Body=file_bytes,
            ContentType=(
                "application/pdf" if file_format == "pdf" else "application/epub+zip"
            ),
            Metadata={"source": "processed", "format": file_format},
        )

        # Update DB with edit history
        asyncio.run(
            _update_book_file_with_history(
                book_id, final_key, file_format, user_id, entity_version
            )
        )

        # Clean up temp upload (keep old book files for rollback)
        try:
            s3.delete_object(Bucket=bucket, Key=s3_key)
        except Exception:
            pass

        return {"status": "ok", "final_key": final_key, "file_format": file_format}
    finally:
        import os

        try:
            os.unlink(tmp_path)
        except Exception:
            pass
