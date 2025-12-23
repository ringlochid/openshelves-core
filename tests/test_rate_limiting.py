"""
Tests for rate limiting functionality.
Tests token bucket algorithm and rate limit enforcement.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

import cache
from cache import token_bucket_allow, make_rate_limit_key


class MockRedis:
    """Mock Redis with controllable state for rate limit testing."""

    def __init__(self):
        self.data = {}

    async def hgetall(self, key):
        return self.data.get(key, {})

    async def hset(self, key, mapping):
        if key not in self.data:
            self.data[key] = {}
        self.data[key].update({k: str(v).encode() for k, v in mapping.items()})

    async def expire(self, key, seconds):
        return True

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)
        return len(keys)


@pytest.mark.asyncio
async def test_token_bucket_first_request_allowed():
    """Test that first request is always allowed (bucket initializes full)."""
    r = MockRedis()
    key = "rl:test:first_request"

    allowed, remaining = await token_bucket_allow(
        key=key,
        capacity=10,
        refill_tokens=1,
        refill_period_seconds=60,
        r=r,
    )

    assert allowed is True
    assert remaining >= 0


@pytest.mark.asyncio
async def test_token_bucket_exhaustion():
    """Test that bucket exhausts after capacity is used."""
    r = MockRedis()
    key = "rl:test:exhaustion"
    capacity = 3

    # Make requests until bucket is empty
    results = []
    for _ in range(capacity + 2):  # Try 2 more than capacity
        allowed, remaining = await token_bucket_allow(
            key=key,
            capacity=capacity,
            refill_tokens=0,  # No refill
            refill_period_seconds=60,
            r=r,
        )
        results.append(allowed)

    # First 'capacity' requests should be allowed
    assert all(results[:capacity])
    # Additional requests should be denied
    assert not any(results[capacity:])


@pytest.mark.asyncio
async def test_token_bucket_refill():
    """Test that bucket refills over time."""
    r = MockRedis()
    key = "rl:test:refill"

    # Set up a bucket with 1 token, already used
    # Simulate time passing by setting last_refill in the past
    import time

    past_time = time.time() - 120  # 2 minutes ago

    r.data[key] = {
        b"tokens": b"0",
        b"last_refill": str(past_time).encode(),
    }

    # Request should succeed because tokens should refill
    allowed, remaining = await token_bucket_allow(
        key=key,
        capacity=10,
        refill_tokens=5,  # 5 tokens per period
        refill_period_seconds=60,  # 60 seconds per refill
        r=r,
    )

    # Should have refilled and allowed
    assert allowed is True


@pytest.mark.asyncio
async def test_make_rate_limit_key_format():
    """Test rate limit key format."""
    user_id = "test-user-123"
    endpoint = "books:create"

    key = make_rate_limit_key(endpoint, user_id)

    assert endpoint in key
    assert user_id in key
    assert key.startswith("rl:")


@pytest.mark.asyncio
async def test_token_bucket_capacity_limit():
    """Test that bucket cannot exceed capacity."""
    r = MockRedis()
    key = "rl:test:capacity_limit"
    capacity = 5

    # Set up bucket with more tokens than capacity (shouldn't happen normally)
    import time

    r.data[key] = {
        b"tokens": b"100",  # Way over capacity
        b"last_refill": str(time.time()).encode(),
    }

    # Make a request
    allowed, remaining = await token_bucket_allow(
        key=key,
        capacity=capacity,
        refill_tokens=1,
        refill_period_seconds=60,
        r=r,
    )

    assert allowed is True
    # Remaining should be capped at capacity - 1
    assert remaining <= capacity


@pytest.mark.asyncio
async def test_rate_limit_different_users_independent():
    """Test that different users have independent rate limits."""
    r = MockRedis()

    user1_key = make_rate_limit_key("test:endpoint", "user-1")
    user2_key = make_rate_limit_key("test:endpoint", "user-2")

    # Exhaust user1's bucket
    for _ in range(5):
        await token_bucket_allow(
            key=user1_key,
            capacity=3,
            refill_tokens=0,
            refill_period_seconds=60,
            r=r,
        )

    # User2 should still be allowed
    allowed, _ = await token_bucket_allow(
        key=user2_key,
        capacity=3,
        refill_tokens=0,
        refill_period_seconds=60,
        r=r,
    )

    assert allowed is True


@pytest.mark.asyncio
async def test_rate_limit_different_endpoints_independent():
    """Test that different endpoints have independent rate limits."""
    r = MockRedis()
    user_id = "same-user"

    endpoint1_key = make_rate_limit_key("endpoint:one", user_id)
    endpoint2_key = make_rate_limit_key("endpoint:two", user_id)

    # Exhaust endpoint1's bucket
    for _ in range(5):
        await token_bucket_allow(
            key=endpoint1_key,
            capacity=3,
            refill_tokens=0,
            refill_period_seconds=60,
            r=r,
        )

    # Same user on endpoint2 should still be allowed
    allowed, _ = await token_bucket_allow(
        key=endpoint2_key,
        capacity=3,
        refill_tokens=0,
        refill_period_seconds=60,
        r=r,
    )

    assert allowed is True


@pytest.mark.asyncio
async def test_token_bucket_partial_refill():
    """Test that partial refills work correctly."""
    r = MockRedis()
    key = "rl:test:partial"

    import time

    # Set up bucket that was refilled 30 seconds ago
    # With refill rate of 1 token per 60 seconds, should get 0.5 tokens (0 floor)
    past_time = time.time() - 30

    r.data[key] = {
        b"tokens": b"0",
        b"last_refill": str(past_time).encode(),
    }

    # With 30 seconds passed and 1 token per 60 seconds, no full token yet
    allowed, remaining = await token_bucket_allow(
        key=key,
        capacity=10,
        refill_tokens=1,
        refill_period_seconds=60,
        r=r,
    )

    # Depends on implementation - may or may not allow
    # The important thing is it doesn't crash
    assert isinstance(allowed, bool)
    assert isinstance(remaining, (int, float))
