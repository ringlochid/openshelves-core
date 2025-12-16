"""
Tests for cache layer with Redis.
Validates caching, versioning, and invalidation strategies.
Uses FakeRedis to avoid event loop issues.
"""
import pytest
import json
from cache import (
    get_cache_version,
    bump_cache_version,
    make_cache_key,
    make_list_key,
    cache_entity,
    get_entity,
    invalidate_entity,
    cache_list,
    get_list,
    cache_author,
    get_author,
    invalidate_author,
    invalidate_author_follows,
    cache_book,
    get_book,
    invalidate_book,
    cache_reviews,
    get_reviews,
    invalidate_reviews,
)

# Note: redis fixture is provided by conftest.py (FakeRedis)


# ========================================
# CACHE VERSION TESTS
# ========================================

@pytest.mark.asyncio
async def test_cache_version_default(redis):
    """Test cache version starts at 1."""
    version = await get_cache_version("test:namespace", redis)
    assert version == 1


@pytest.mark.asyncio
async def test_bump_cache_version(redis):
    """Test bumping cache version increments correctly."""
    v1 = await bump_cache_version("test:namespace", redis)
    assert v1 == 1
    
    v2 = await bump_cache_version("test:namespace", redis)
    assert v2 == 2
    
    v3 = await get_cache_version("test:namespace", redis)
    assert v3 == 2


@pytest.mark.asyncio
async def test_bump_invalidates_old_lists(redis):
    """Test that bumping version invalidates old list keys."""
    namespace = "test:lists"
    params = {"status": "APPROVED", "limit": 20}
    
    # Initialize version by bumping (flushdb cleared it)
    v1 = await bump_cache_version(namespace, redis)
    assert v1 == 1
    
    # Cache list at version 1
    await cache_list(namespace, params, {"items": [1, 2, 3]}, v1, redis)
    
    # Retrieve at version 1 (should work)
    cached = await get_list(namespace, params, v1, redis)
    assert cached == {"items": [1, 2, 3]}
    
    # Bump version again
    v2 = await bump_cache_version(namespace, redis)
    assert v2 == 2
    
    # When getting list without specifying version, it uses new version
    cached_current = await get_list(namespace, params, r=redis)
    assert cached_current is None  # Not cached at v2 yet
    
    # Old version key still physically exists but won't be used
    # (we specify v1 explicitly to verify old key still there)
    cached_old_explicit = await get_list(namespace, params, v1, redis)
    assert cached_old_explicit == {"items": [1, 2, 3]}  # Still there if we ask for v1
    
    # But default behavior uses current version (v2), which is empty
    v_current = await get_cache_version(namespace, redis)
    assert v_current == 2


# ========================================
# KEY GENERATION TESTS
# ========================================

def test_make_cache_key():
    """Test entity cache key generation."""
    assert make_cache_key("author", 123) == "author:123"
    assert make_cache_key("book", "abc") == "book:abc"


def test_make_list_key():
    """Test list cache key generation with params."""
    params1 = {"status": "APPROVED", "limit": 20}
    key1 = make_list_key("authors:list", params1, 1)
    assert key1.startswith("authors:list:v1:")
    
    # Same params should generate same key
    key2 = make_list_key("authors:list", params1, 1)
    assert key1 == key2
    
    # Different params should generate different key
    params2 = {"status": "PENDING", "limit": 20}
    key3 = make_list_key("authors:list", params2, 1)
    assert key3 != key1


def test_make_list_key_ignores_none():
    """Test that None values are filtered from params."""
    params1 = {"status": "APPROVED", "search": None, "limit": 20}
    params2 = {"status": "APPROVED", "limit": 20}
    
    key1 = make_list_key("authors:list", params1, 1)
    key2 = make_list_key("authors:list", params2, 1)
    
    # Should generate same key (None is ignored)
    assert key1 == key2


# ========================================
# ENTITY CACHING TESTS
# ========================================

@pytest.mark.asyncio
async def test_cache_and_get_entity(redis):
    """Test caching and retrieving an entity."""
    data = {"id": 1, "name": "Test Author", "email": "test@example.com"}
    
    await cache_entity("author", 1, data, redis)
    cached = await get_entity("author", 1, redis)
    
    assert cached == data


@pytest.mark.asyncio
async def test_get_entity_not_cached(redis):
    """Test getting non-existent entity returns None."""
    cached = await get_entity("author", 999, redis)
    assert cached is None


@pytest.mark.asyncio
async def test_invalidate_entity(redis):
    """Test entity invalidation."""
    data = {"id": 1, "name": "Test Author"}
    
    await cache_entity("author", 1, data, redis)
    assert await get_entity("author", 1, redis) == data
    
    await invalidate_entity("author", 1, redis)
    assert await get_entity("author", 1, redis) is None


# ========================================
# LIST CACHING TESTS
# ========================================

@pytest.mark.asyncio
async def test_cache_and_get_list(redis):
    """Test caching and retrieving a list query."""
    namespace = "authors:list"
    params = {"status": "APPROVED", "limit": 20}
    data = {"items": [{"id": 1}, {"id": 2}], "next_cursor": "abc"}
    
    await cache_list(namespace, params, data, r=redis)
    cached = await get_list(namespace, params, r=redis)
    
    assert cached == data


@pytest.mark.asyncio
async def test_get_list_not_cached(redis):
    """Test getting non-existent list returns None."""
    cached = await get_list("authors:list", {"limit": 10}, r=redis)
    assert cached is None


@pytest.mark.asyncio
async def test_list_cache_with_different_params(redis):
    """Test that different params create separate cache entries."""
    namespace = "authors:list"
    
    data1 = {"items": [1, 2, 3]}
    params1 = {"status": "APPROVED"}
    await cache_list(namespace, params1, data1, r=redis)
    
    data2 = {"items": [4, 5, 6]}
    params2 = {"status": "PENDING"}
    await cache_list(namespace, params2, data2, r=redis)
    
    cached1 = await get_list(namespace, params1, r=redis)
    cached2 = await get_list(namespace, params2, r=redis)
    
    assert cached1 == data1
    assert cached2 == data2
    assert cached1 != cached2


# ========================================
# AUTHOR CACHING TESTS
# ========================================

@pytest.mark.asyncio
async def test_cache_author(redis):
    """Test author-specific caching."""
    data = {"id": 1, "name": "Tolkien", "bio": "Author of LOTR"}
    
    await cache_author(1, data, redis)
    cached = await get_author(1, redis)
    
    assert cached == data


@pytest.mark.asyncio
async def test_invalidate_author_bumps_list_version(redis):
    """Test that invalidating author bumps list cache versions."""
    namespace = "authors:list"
    
    # Cache author and list
    author_data = {"id": 1, "name": "Tolkien"}
    list_data = {"items": [{"id": 1}]}
    
    # Initialize version (flushdb cleared it)
    await bump_cache_version(namespace, redis)
    v1 = await get_cache_version(namespace, redis)
    
    await cache_author(1, author_data, redis)
    await cache_list(namespace, {}, list_data, v1, redis)
    
    # Verify cached
    assert await get_author(1, redis) == author_data
    assert await get_list(namespace, {}, v1, redis) == list_data
    
    # Invalidate author
    await invalidate_author(1, redis)
    
    # Author should be gone
    assert await get_author(1, redis) is None
    
    # List version should be bumped
    v2 = await get_cache_version(namespace, redis)
    assert v2 == v1 + 1
    
    # Default get_list (no version) uses new version, which has no data
    assert await get_list(namespace, {}, r=redis) is None


@pytest.mark.asyncio
async def test_invalidate_author_follows(redis):
    """Test that follow invalidation clears author detail."""
    author_data = {"id": 1, "name": "Tolkien", "follower_count": 5}
    
    await cache_author(1, author_data, redis)
    assert await get_author(1, redis) == author_data
    
    await invalidate_author_follows(1, redis)
    assert await get_author(1, redis) is None


# ========================================
# BOOK CACHING TESTS
# ========================================

@pytest.mark.asyncio
async def test_cache_book(redis):
    """Test book-specific caching."""
    data = {"id": 1, "title": "The Hobbit", "isbn": "978-0-618-00221-3"}
    
    await cache_book(1, data, redis)
    cached = await get_book(1, redis)
    
    assert cached == data


@pytest.mark.asyncio
async def test_invalidate_book_bumps_list_version(redis):
    """Test that invalidating book bumps books list version."""
    namespace = "books:list"
    
    # Initialize version (flushdb cleared it)
    await bump_cache_version(namespace, redis)
    v1 = await get_cache_version(namespace, redis)
    
    book_data = {"id": 1, "title": "The Hobbit"}
    await cache_book(1, book_data, redis)
    
    # Invalidate book (pass empty author_ids to skip DB query in tests)
    await invalidate_book(1, redis, author_ids=[])
    
    # Book should be gone
    assert await get_book(1, redis) is None
    
    # Version should be bumped
    v2 = await get_cache_version(namespace, redis)
    assert v2 == v1 + 1


# ========================================
# REVIEW CACHING TESTS
# ========================================

@pytest.mark.asyncio
async def test_cache_reviews(redis):
    """Test review caching for a book."""
    reviews_data = [
        {"id": 1, "rating": 5, "comment": "Great book!"},
        {"id": 2, "rating": 4, "comment": "Good read"},
    ]
    
    await cache_reviews(1, reviews_data, redis)
    cached = await get_reviews(1, redis)
    
    assert cached == reviews_data


@pytest.mark.asyncio
async def test_invalidate_reviews(redis):
    """Test review cache invalidation."""
    reviews_data = [{"id": 1, "rating": 5}]
    
    await cache_reviews(1, reviews_data, redis)
    assert await get_reviews(1, redis) == reviews_data
    
    await invalidate_reviews(1, redis)
    assert await get_reviews(1, redis) is None


# ========================================
# INTEGRATION TESTS
# ========================================

@pytest.mark.asyncio
async def test_cache_workflow_author_update(redis):
    """Test full cache workflow when updating an author."""
    # Step 1: Cache author and list
    author_data = {"id": 1, "name": "Tolkien", "email": "tolkien@example.com"}
    list_data = {"items": [{"id": 1, "name": "Tolkien"}]}
    
    await cache_author(1, author_data, redis)
    await cache_list("authors:list", {"status": "APPROVED"}, list_data, r=redis)
    
    # Step 2: Update author (simulate endpoint mutation)
    # Invalidate caches (pass empty book_ids to skip DB query in tests)
    await invalidate_author(1, redis, book_ids=[])
    
    # Step 3: Verify old data is gone
    assert await get_author(1, redis) is None
    
    # Step 4: Cache new data
    updated_data = {**author_data, "email": "newemail@example.com"}
    await cache_author(1, updated_data, redis)
    
    # Step 5: Verify new data is cached
    cached = await get_author(1, redis)
    assert cached["email"] == "newemail@example.com"
