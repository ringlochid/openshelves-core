from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from settings import settings

# Async engine for application
async_engine = create_async_engine(
    settings.DATABASE_ASYNC_URL,
    echo=False,
    future=True,
    pool_size=10,  # Increased for better concurrency
    max_overflow=20,
    pool_pre_ping=True,  # Verify connections before use
    pool_recycle=3600,  # Recycle connections after 1 hour
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
)

# Sync engine for Alembic migrations only
sync_url = settings.DATABASE_ASYNC_URL.replace(
    "postgresql+asyncpg://", "postgresql+psycopg://"
)
sync_engine = create_engine(
    sync_url,
    echo=False,
    future=True,
    pool_size=5,  # Smaller pool for migrations
    max_overflow=5,
    pool_pre_ping=True,
)

Base = declarative_base()


async def get_async_db():
    """Async database session dependency."""
    async with AsyncSessionLocal() as db:
        yield db


def create_worker_session():
    """
    Create fresh async engine and session factory for Celery worker use.

    This avoids event loop conflicts that occur when reusing the global
    AsyncSessionLocal which is bound to the import-time event loop.

    Celery tasks run with asyncio.run() which creates a new event loop.
    Using the global session factory causes: RuntimeError: no running event loop.

    Returns:
        Tuple of (session_factory, engine) - caller must dispose engine when done.
    """
    worker_engine = create_async_engine(
        settings.DATABASE_ASYNC_URL,
        echo=False,
        future=True,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    worker_session = async_sessionmaker(
        bind=worker_engine,
        autoflush=False,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    return worker_session, worker_engine
