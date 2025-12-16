"""
Simplified Redis cache layer for Library Service.
Handles caching of authors, books, and lists with automatic invalidation.
"""
import hashlib
import json
from typing import Any

from fastapi import Request
from redis import asyncio as aioredis

from settings import settings

Redis = aioredis.Redis
from_url = aioredis.from_url

_redis: Redis | None = None
DEFAULT_TTL = settings.DEFAULT_CACHE_TTL  # 300 seconds (5 minutes)
VERSION_KEY_PREFIX = "cache:version:"


# ========================================
# REDIS CONNECTION MANAGEMENT
# ========================================

async def init_redis() -> Redis:
    """Initialize singleton Redis client."""
    global _redis
    if _redis is None:
        _redis = from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _redis


async def close_redis():
    """Close Redis connection."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def get_redis(request: Request) -> Redis:
    """FastAPI dependency: return app-scoped Redis client from lifespan."""
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        # Fallback: initialize if not set (shouldn't happen in production)
        redis_client = await init_redis()
        request.app.state.redis = redis_client
    return redis_client


# ========================================
# CACHE VERSION MANAGEMENT
# ========================================

async def get_cache_version(name: str, r: Redis | None = None) -> int:
    """Get current cache version for a namespace."""
    r = r or await init_redis()
    raw = await r.get(f"{VERSION_KEY_PREFIX}{name}")
    return int(raw) if raw is not None else 1


async def bump_cache_version(name: str, r: Redis | None = None) -> int:
    """
    Increment cache version to invalidate all keys in a namespace.
    More efficient than deleting individual keys.
    """
    r = r or await init_redis()
    return int(await r.incr(f"{VERSION_KEY_PREFIX}{name}"))


# ========================================
# CACHE KEY GENERATION
# ========================================

def make_cache_key(namespace: str, identifier: str | int) -> str:
    """Generate cache key for a single entity."""
    return f"{namespace}:{identifier}"


def make_list_key(namespace: str, params: dict, version: int = 1) -> str:
    """Generate cache key for a list query with params."""
    # Normalize params: remove None values, sort keys
    clean = {k: v for k, v in params.items() if v is not None}
    payload = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha1(payload.encode()).hexdigest()[:16]
    return f"{namespace}:v{version}:{h}"


# ========================================
# ENTITY CACHING (Author, Book, Collection)
# ========================================

async def cache_entity(
    namespace: str,
    entity_id: int,
    data: dict | list[dict],
    r: Redis | None = None,
    ttl: int = DEFAULT_TTL
):
    """Cache a single entity (author, book, collection) or list (reviews)."""
    r = r or await init_redis()
    key = make_cache_key(namespace, entity_id)
    await r.set(key, json.dumps(data), ex=ttl)


async def get_entity(
    namespace: str,
    entity_id: int,
    r: Redis | None = None
) -> dict | list[dict] | None:
    """Get cached entity or list."""
    r = r or await init_redis()
    key = make_cache_key(namespace, entity_id)
    raw = await r.get(key)
    return json.loads(raw) if raw else None


async def invalidate_entity(
    namespace: str,
    entity_id: int,
    r: Redis | None = None
):
    """Invalidate a single entity cache."""
    r = r or await init_redis()
    key = make_cache_key(namespace, entity_id)
    await r.delete(key)


# ========================================
# LIST CACHING (with versioning)
# ========================================

async def cache_list(
    namespace: str,
    params: dict,
    data: Any,
    version: int | None = None,
    r: Redis | None = None,
    ttl: int = DEFAULT_TTL
):
    """Cache a list query result."""
    r = r or await init_redis()
    if version is None:
        version = await get_cache_version(namespace, r)
    
    key = make_list_key(namespace, params, version)
    await r.set(key, json.dumps(data), ex=ttl)


async def get_list(
    namespace: str,
    params: dict,
    version: int | None = None,
    r: Redis | None = None
) -> Any | None:
    """Get cached list query result."""
    r = r or await init_redis()
    if version is None:
        version = await get_cache_version(namespace, r)
    
    key = make_list_key(namespace, params, version)
    raw = await r.get(key)
    return json.loads(raw) if raw else None


# ========================================
# RELATIONSHIP HELPERS (for cascading invalidation)
# ========================================

async def get_author_book_ids(author_id: int) -> list[int]:
    """
    Get IDs of books associated with an author.
    Used for cascading cache invalidation.
    """
    from database import AsyncSessionLocal
    from models import Author
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    
    async with AsyncSessionLocal() as db:
        query = select(Author).where(Author.id == author_id).options(selectinload(Author.books))
        result = await db.execute(query)
        author = result.scalar_one_or_none()
        
        if author and author.books:
            return [book.id for book in author.books]
        return []


async def get_book_author_ids(book_id: int) -> list[int]:
    """
    Get IDs of authors associated with a book.
    Used for cascading cache invalidation.
    """
    from database import AsyncSessionLocal
    from models import Book
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    
    async with AsyncSessionLocal() as db:
        query = select(Book).where(Book.id == book_id).options(selectinload(Book.authors))
        result = await db.execute(query)
        book = result.scalar_one_or_none()
        
        if book and book.authors:
            return [author.id for author in book.authors]
        return []


# ========================================
# AUTHOR CACHING
# ========================================

async def cache_author(author_id: int, data: dict, r: Redis | None = None):
    """Cache author entity."""
    await cache_entity("author", author_id, data, r)


async def get_author(author_id: int, r: Redis | None = None) -> dict | None:
    """Get cached author."""
    result = await get_entity("author", author_id, r)
    # Type narrowing: authors are always dict, never list
    return result if isinstance(result, dict) or result is None else None


async def invalidate_author(author_id: int, r: Redis | None = None, book_ids: list[int] | None = None):
    """
    Invalidate author cache and all lists containing authors.
    Also invalidates related books (cascading invalidation).
    
    Bumps version to invalidate: GET /authors, GET /jury/authors
    
    Args:
        author_id: ID of author to invalidate
        r: Redis connection (optional)
        book_ids: List of related book IDs (optional, will query DB if not provided)
    """
    r = r or await init_redis()
    
    # Invalidate author entity
    await invalidate_entity("author", author_id, r)
    
    # Invalidate author lists
    await bump_cache_version("authors:list", r)
    await bump_cache_version("jury:authors", r)
    
    # Cascade: invalidate related books (books show author info in detail view)
    if book_ids is None:
        book_ids = await get_author_book_ids(author_id)
    
    for book_id in book_ids:
        await invalidate_entity("book", book_id, r)


async def invalidate_author_follows(author_id: int, r: Redis | None = None):
    """Invalidate follow-related caches for an author."""
    r = r or await init_redis()
    # Invalidate author detail (includes follower_count)
    await invalidate_entity("author", author_id, r)


# ========================================
# BOOK CACHING
# ========================================

async def cache_book(book_id: int, data: dict, r: Redis | None = None):
    """Cache book entity."""
    await cache_entity("book", book_id, data, r)


async def get_book(book_id: int, r: Redis | None = None) -> dict | None:
    """Get cached book."""
    result = await get_entity("book", book_id, r)
    # Type narrowing: books are always dict, never list
    return result if isinstance(result, dict) or result is None else None


async def invalidate_book(book_id: int, r: Redis | None = None, author_ids: list[int] | None = None):
    """
    Invalidate book cache and all lists containing books.
    Also invalidates related authors and reviews (cascading invalidation).
    
    Bumps version to invalidate: GET /books, GET /authors/{id}/books
    
    Args:
        book_id: ID of book to invalidate
        r: Redis connection (optional)
        author_ids: List of related author IDs (optional, will query DB if not provided)
    """
    r = r or await init_redis()
    
    # Invalidate book entity
    await invalidate_entity("book", book_id, r)
    
    # Invalidate book lists
    await bump_cache_version("books:list", r)
    
    # Cascade: invalidate related authors (author detail shows book list)
    # Note: author_ids should always be provided from calling context to avoid
    # creating new DB sessions (can cause event loop issues in tests)
    if author_ids:
        for author_id in author_ids:
            await invalidate_entity("author", author_id, r)
    
    # Cascade: invalidate book reviews
    await invalidate_entity("reviews", book_id, r)


# ========================================
# COLLECTION CACHING
# ========================================

async def cache_collection(collection_id: int, data: dict, r: Redis | None = None):
    """Cache collection entity."""
    await cache_entity("collection", collection_id, data, r)


async def get_collection(collection_id: int, r: Redis | None = None) -> dict | None:
    """Get cached collection."""
    result = await get_entity("collection", collection_id, r)
    # Type narrowing: collections are always dict, never list
    return result if isinstance(result, dict) or result is None else None


async def invalidate_collection(collection_id: int, r: Redis | None = None):
    """Invalidate collection cache and lists."""
    r = r or await init_redis()
    await invalidate_entity("collection", collection_id, r)
    await bump_cache_version("collections:list", r)


# ========================================
# REVIEW CACHING
# ========================================

async def cache_reviews(book_id: int, data: list[dict], r: Redis | None = None):
    """Cache reviews for a book."""
    await cache_entity("reviews", book_id, data, r)


async def get_reviews(book_id: int, r: Redis | None = None) -> list[dict] | None:
    """Get cached reviews for a book."""
    result = await get_entity("reviews", book_id, r)
    # Type narrowing: reviews are always list, never single dict
    return result if isinstance(result, list) or result is None else None


async def invalidate_reviews(book_id: int, r: Redis | None = None):
    """Invalidate reviews cache for a book."""
    await invalidate_entity("reviews", book_id, r)
