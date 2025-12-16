"""
Test for book search trigram fallback.

Ensures zero-result FTS searches fall back to trigram similarity as documented.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, Book, ContentStatus


@pytest.mark.asyncio
async def test_search_falls_back_to_trigram_when_fts_returns_nothing(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """
    Verify documented behavior: when FTS finds nothing, search falls back to trigram similarity.
    
    Test scenario:
    - Create a book with title "Machine Learning Basics"
    - Search for "Mashin Lerning" (typos that FTS won't match)
    - Trigram similarity should still find the book
    """
    # Create author
    author = Author(
        name="Dr. AI Researcher",
        email="researcher@example.com",
        created_by_user_id=uuid4(),
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    # Create book with specific title
    book = Book(
        title="Machine Learning Basics",
        year=2023,
        file_format="pdf",
        tags=[],
        status=ContentStatus.APPROVED,
        is_public=True,
        created_by_user_id=uuid4(),
    )
    book.authors.append(author)
    test_db.add(book)
    await test_db.commit()
    await test_db.refresh(book)
    
    # Search with typos that FTS won't match but trigram should
    response = await async_client.get("/books", params={"q": "Mashin Lerning"})
    
    assert response.status_code == 200
    response_data = response.json()
    results = response_data["items"]
    
    # Should find the book via trigram fallback
    assert len(results) > 0
    found_book = next((b for b in results if b["id"] == book.id), None)
    assert found_book is not None
    assert found_book["title"] == "Machine Learning Basics"


@pytest.mark.asyncio
async def test_search_fts_works_for_exact_matches(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """
    Verify FTS still works for good queries (shouldn't always use fallback).
    """
    # Create author and book
    author = Author(
        name="Python Expert",
        email="expert@example.com",
        created_by_user_id=uuid4(),
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    book = Book(
        title="Python Programming Guide",
        year=2024,
        file_format="pdf",        tags=[],        status=ContentStatus.APPROVED,
        is_public=True,
        created_by_user_id=uuid4(),
    )
    book.authors.append(author)
    test_db.add(book)
    await test_db.commit()
    await test_db.refresh(book)
    
    # Search with exact term
    response = await async_client.get("/books", params={"q": "Python Programming"})
    
    assert response.status_code == 200
    response_data = response.json()
    results = response_data["items"]
    assert len(results) > 0
    
    found_book = next((b for b in results if b["id"] == book.id), None)
    assert found_book is not None


@pytest.mark.asyncio
async def test_search_returns_empty_for_completely_irrelevant_query(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """
    Verify that completely irrelevant searches still return empty (trigram threshold works).
    """
    # Create author and book
    author = Author(
        name="Biology Professor",
        email="bio@example.com",
        created_by_user_id=uuid4(),
        status=ContentStatus.APPROVED,
        is_public=True,
    )
    test_db.add(author)
    await test_db.flush()
    await test_db.refresh(author)
    
    book = Book(
        title="Cell Biology Textbook",
        year=2024,
        file_format="pdf",        tags=[],        status=ContentStatus.APPROVED,
        is_public=True,
        created_by_user_id=uuid4(),
    )
    book.authors.append(author)
    test_db.add(book)
    await test_db.commit()
    
    # Search for something completely unrelated
    response = await async_client.get("/books", params={"q": "Quantum Mechanics Physics"})
    
    assert response.status_code == 200
    response_data = response.json()
    results = response_data["items"]
    
    # Should not find the biology book (similarity too low)
    found_book = next((b for b in results if b["id"] == book.id), None)
    assert found_book is None
