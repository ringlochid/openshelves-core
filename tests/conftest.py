"""
Test configuration and fixtures for library service tests.
"""
import pytest
import asyncio
from unittest.mock import patch
from uuid import uuid4

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

import main
from database import get_async_db, Base
from models import ContentStatus

app = main.app


# ========================================
# DATABASE FIXTURES
# ========================================

# Import settings for real database URL
from settings import settings

# Use real PostgreSQL database for testing
TEST_DATABASE_URL = settings.DATABASE_ASYNC_URL


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def db():
    """
    Create a test database session using the real PostgreSQL database.
    Uses a transaction that is rolled back after each test.
    """
    # Create engine
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    
    # Create session factory
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session_maker() as session:
        # Start a transaction
        async with session.begin():
            yield session
            # Rollback after test
            await session.rollback()
    
    await engine.dispose()


@pytest.fixture
async def client(db: AsyncSession):
    """Create an async test client with overridden database dependency."""
    
    async def override_get_db():
        yield db
    
    app.dependency_overrides[get_async_db] = override_get_db
    
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac
    
    app.dependency_overrides.clear()


# ========================================
# AUTH FIXTURES
# ========================================

@pytest.fixture
def mock_jwt_user():
    """Mock JWT user with standard scopes."""
    user_data = {
        "user_id": uuid4(),
        "username": "testuser",
        "email": "test@example.com",
        "scopes": ["content:submit", "content:edit_any"],
        "roles": ["contributor"],
        "trust_score": 50,
    }
    
    # Patch all auth dependencies
    with patch("dependencies.auth.get_current_user", return_value=user_data), \
         patch("dependencies.auth.require_scope", return_value=user_data), \
         patch("dependencies.auth.require_role", return_value=user_data), \
         patch("dependencies.auth.require_min_trust", return_value=user_data):
        yield user_data


@pytest.fixture
def mock_admin_user():
    """Mock JWT user with admin role."""
    user_data = {
        "user_id": uuid4(),
        "username": "adminuser",
        "email": "admin@example.com",
        "scopes": ["content:submit", "content:edit_any", "content:delete_any"],
        "roles": ["admin"],
        "trust_score": 100,
    }
    
    # Patch all auth dependencies
    with patch("dependencies.auth.get_current_user", return_value=user_data), \
         patch("dependencies.auth.require_scope", return_value=user_data), \
         patch("dependencies.auth.require_role", return_value=user_data), \
         patch("dependencies.auth.require_min_trust", return_value=user_data):
        yield user_data
