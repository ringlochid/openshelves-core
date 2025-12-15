import hashlib
import json
from typing import Any, Iterable

from fastapi import Request
from redis import asyncio as aioredis

from settings import settings

Redis = aioredis.Redis
from_url = aioredis.from_url

_redis: Redis | None = None
VERSION_KEY_PREFIX = "cache:version:"
DEFAULT_TTL = settings.DEFAULT_CACHE_TTL


async def get_cache_version(name: str, r: Redis | None = None) -> int:
    r = r or await init_redis()
    raw = await r.get(f"{VERSION_KEY_PREFIX}{name}")
    return int(raw) if raw is not None else 1


async def bump_cache_version(name: str, r: Redis | None = None) -> int:
    r = r or await init_redis()
    return int(await r.incr(f"{VERSION_KEY_PREFIX}{name}"))


async def init_redis() -> Redis:
    """Create a single async Redis client for the process."""
    global _redis
    if _redis is None:
        _redis = from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _redis


async def close_redis():
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None


async def get_redis(request: Request) -> Redis:
    """FastAPI dependency: return app-scoped Redis client."""
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        redis_client = await init_redis()
        request.app.state.redis = redis_client
    return redis_client


def normalize_params(params: dict) -> tuple[dict, str]:
    clean = {k: v for k, v in params.items() if v is not None}
    payload = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return clean, payload


def make_list_key(prefix: str, params: dict, version: int = 1) -> str:
    _, payload = normalize_params(params)
    h = hashlib.sha1(payload.encode()).hexdigest()[:16]  # trim to 16
    return f"{prefix}:v{version}:{h}"


def make_list_key_with_payload(
    prefix: str, params: dict, version: int = 1
) -> tuple[str, str]:
    clean, payload = normalize_params(params)
    h = hashlib.sha1(payload.encode()).hexdigest()[:16]
    return f"{prefix}:v{version}:{h}", payload


async def make_books_list_key(
    params: dict, version: int | None = None, r: Redis | None = None
):
    if version is None:
        version = await get_cache_version("books:list", r)
    return make_list_key_with_payload("books:list", params, version)


async def make_authors_list_key(
    params: dict, version: int | None = None, r: Redis | None = None
) -> tuple[str, str]:
    if version is None:
        version = await get_cache_version("authors:list", r)
    return make_list_key_with_payload("authors:list", params, version)


def make_book_key(book_id: int) -> str:
    return f"book:{book_id}"


def make_author_key(author_id: int) -> str:
    return f"author:{author_id}"


def make_author_books_key(author_id: int) -> str:
    return f"author:{author_id}:books"


def make_reviews_key(book_id: int) -> str:
    return f"book:{book_id}:reviews"


async def cache_book(
    book_id: int, book_data: dict, r: Redis | None = None, ttl: int = DEFAULT_TTL
):
    r = r or await init_redis()
    await r.set(make_book_key(book_id), json.dumps(book_data), ex=ttl)


async def get_book(book_id: int, r: Redis | None = None) -> dict | None:
    r = r or await init_redis()
    raw = await r.get(make_book_key(book_id))
    return json.loads(raw) if raw else None


async def cache_author(
    author_id: int, author_data: dict, r: Redis | None = None, ttl: int = DEFAULT_TTL
):
    r = r or await init_redis()
    await r.set(make_author_key(author_id), json.dumps(author_data), ex=ttl)


async def get_author(author_id: int, r: Redis | None = None) -> dict | None:
    r = r or await init_redis()
    raw = await r.get(make_author_key(author_id))
    return json.loads(raw) if raw else None


async def cache_list(
    key: str, data: Any, r: Redis | None = None, ttl: int = DEFAULT_TTL
):
    r = r or await init_redis()
    await r.set(key, json.dumps(data), ex=ttl)


async def get_list(key: str, r: Redis | None = None) -> Any | None:
    r = r or await init_redis()
    raw = await r.get(key)
    return json.loads(raw) if raw else None


async def cache_list_with_params(
    key: str,
    data: Any,
    params_payload: str,
    r: Redis | None = None,
    ttl: int = DEFAULT_TTL,
):
    """Store list data alongside normalized params to guard against collisions."""
    await cache_list(key, {"data": data, "_params": params_payload}, r=r, ttl=ttl)


async def get_list_with_params(
    key: str, expected_params_payload: str, r: Redis | None = None
) -> Any | None:
    """Return cached list only if params match; otherwise treat as miss."""
    cached = await get_list(key, r=r)
    if isinstance(cached, dict) and cached.get("_params") == expected_params_payload:
        return cached.get("data")
    return None


async def link_book_to_authors(
    book_id: int, author_ids: Iterable[int], r: Redis | None = None
):
    r = r or await init_redis()
    for aid in author_ids:
        await r.sadd(make_author_books_key(aid), book_id)


async def get_books_for_author(author_id: int, r: Redis | None = None) -> set[int]:
    r = r or await init_redis()
    return {int(bid) for bid in await r.smembers(make_author_books_key(author_id))}


async def invalidate_book(book_id: int, r: Redis | None = None):
    r = r or await init_redis()
    # In cluster mode, delete keys individually to avoid CROSSSLOT on multi-key DEL.
    for key in (
        make_book_key(book_id),
        make_reviews_key(book_id),
        f"book:{book_id}:authors",
    ):
        await r.delete(key)


async def invalidate_author(
    author_id: int, r: Redis | None = None, book_ids: Iterable[int] | None = None
):
    r = r or await init_redis()
    await r.delete(make_author_key(author_id))
    author_books_key = make_author_books_key(author_id)
    related_book_ids = {int(bid) for bid in book_ids} if book_ids else set()
    if not related_book_ids:
        related_book_ids = {int(bid) for bid in await r.smembers(author_books_key)}

    if related_book_ids:
        keys_to_delete = []
        for bid in related_book_ids:
            keys_to_delete.append(make_book_key(bid))
            keys_to_delete.append(make_reviews_key(bid))
        # Delete keys one by one to avoid Redis cluster cross-slot errors.
        for key in keys_to_delete:
            await r.delete(key)

    await r.delete(author_books_key)
