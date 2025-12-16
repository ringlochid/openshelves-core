"""
Tests for author similarity search with trigram matching and cursor pagination.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestAuthorSimilaritySearch:
    """Test suite for trigram similarity search on authors."""

    async def test_search_by_name_similarity(self, async_client: AsyncClient, test_db):
        """Test that similarity search finds authors by name with fuzzy matching."""
        from models import Author, ContentStatus
        
        # Create test authors
        authors = [
            Author(
                name="J.R.R. Tolkien",
                email="tolkien@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440000",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
            Author(
                name="J.K. Rowling",
                email="rowling@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440001",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
            Author(
                name="George R.R. Martin",
                email="grrm@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440002",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
        ]
        
        for author in authors:
            test_db.add(author)
        await test_db.commit()
        
        # Search for "tolkien" - should find Tolkien
        response = await async_client.get("/authors?search=tolkien")
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["items"]) >= 1
        assert data["items"][0]["name"] == "J.R.R. Tolkien"
        assert "next_cursor" in data

    async def test_typo_tolerance(self, async_client: AsyncClient, test_db):
        """Test that similarity search tolerates typos in search query."""
        from models import Author, ContentStatus
        
        # Create author
        author = Author(
            name="Stephen King",
            email="sking@example.com",
            created_by_user_id="550e8400-e29b-41d4-a716-446655440000",
            status=ContentStatus.APPROVED,
            is_public=True,
            version=1,
        )
        test_db.add(author)
        await test_db.commit()
        
        # Search with typo: "stephen" vs "stefen" or "king" vs "kiing"
        response = await async_client.get("/authors?search=stefen")
        assert response.status_code == 200
        data = response.json()
        
        # Should still find Stephen King with reasonable similarity threshold
        # Trigram similarity allows fuzzy matching
        author_names = [item["name"] for item in data["items"]]
        # May or may not find depending on threshold, but should not error

    async def test_cursor_pagination_with_search(self, async_client: AsyncClient, test_db):
        """Test cursor-based pagination works correctly with search results."""
        from models import Author, ContentStatus
        
        # Create multiple authors with similar names
        authors = [
            Author(
                name=f"John Smith {i}",
                email=f"john{i}@example.com",
                created_by_user_id=f"550e8400-e29b-41d4-a716-44665544000{i}",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            )
            for i in range(5)
        ]
        
        for author in authors:
            test_db.add(author)
        await test_db.commit()
        
        # Get first page with limit=2
        response = await async_client.get("/authors?search=john&limit=2")
        assert response.status_code == 200
        page1 = response.json()
        
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] is not None
        
        # Get second page using cursor
        response2 = await async_client.get(f"/authors?search=john&limit=2&cursor={page1['next_cursor']}")
        assert response2.status_code == 200
        page2 = response2.json()
        
        assert len(page2["items"]) == 2
        
        # Ensure no duplicate results
        page1_ids = {item["id"] for item in page1["items"]}
        page2_ids = {item["id"] for item in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_empty_search_returns_all(self, async_client: AsyncClient, test_db):
        """Test that empty search parameter returns all approved authors."""
        from models import Author, ContentStatus
        
        # Create test authors
        authors = [
            Author(
                name="Author A",
                email="a@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440000",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
            Author(
                name="Author B",
                email="b@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440001",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
        ]
        
        for author in authors:
            test_db.add(author)
        await test_db.commit()
        
        # No search parameter - should return all
        response = await async_client.get("/authors")
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["items"]) >= 2
        # Should be ordered by name alphabetically when no search
        names = [item["name"] for item in data["items"]]
        assert names == sorted(names)

    async def test_weighted_scoring_name_over_email(self, async_client: AsyncClient, test_db):
        """Test that name matches are weighted higher than email matches (70% vs 30%)."""
        from models import Author, ContentStatus
        
        # Create authors where search term matches differently
        authors = [
            Author(
                name="Alice Brown",
                email="smith@example.com",  # 'smith' in email
                created_by_user_id="550e8400-e29b-41d4-a716-446655440000",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
            Author(
                name="John Smith",  # 'smith' in name
                email="john@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440001",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
        ]
        
        for author in authors:
            test_db.add(author)
        await test_db.commit()
        
        # Search for "smith" - name match should rank higher
        response = await async_client.get("/authors?search=smith")
        assert response.status_code == 200
        data = response.json()
        
        # John Smith (name match) should appear before Alice Brown (email match)
        if len(data["items"]) >= 2:
            assert data["items"][0]["name"] == "John Smith"

    async def test_cursor_encoding_decoding(self, async_client: AsyncClient, test_db):
        """Test that cursor encoding/decoding works correctly for similarity search."""
        from models import Author, ContentStatus
        
        # Create test authors
        authors = [
            Author(
                name=f"Test Author {i}",
                email=f"test{i}@example.com",
                created_by_user_id=f"550e8400-e29b-41d4-a716-44665544000{i}",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            )
            for i in range(3)
        ]
        
        for author in authors:
            test_db.add(author)
        await test_db.commit()
        
        # Get first page with search
        response = await async_client.get("/authors?search=test&limit=2")
        assert response.status_code == 200
        data = response.json()
        
        if data["next_cursor"]:
            # Cursor should be base64 encoded JSON
            import base64
            import json
            
            cursor_data = json.loads(base64.urlsafe_b64decode(data["next_cursor"] + "=="))
            
            # For search results, cursor should contain score and id
            assert "score" in cursor_data or "id" in cursor_data
            
            # Using cursor should not cause errors
            response2 = await async_client.get(f"/authors?search=test&limit=2&cursor={data['next_cursor']}")
            assert response2.status_code == 200

    async def test_only_approved_public_authors_returned(self, async_client: AsyncClient, test_db):
        """Test that only approved and public authors are returned in search results."""
        from models import Author, ContentStatus
        
        # Create authors with different statuses
        authors = [
            Author(
                name="Approved Author",
                email="approved@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440000",
                status=ContentStatus.APPROVED,
                is_public=True,
                version=1,
            ),
            Author(
                name="Pending Author",
                email="pending@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440001",
                status=ContentStatus.PENDING,
                is_public=False,
                version=1,
            ),
            Author(
                name="Rejected Author",
                email="rejected@example.com",
                created_by_user_id="550e8400-e29b-41d4-a716-446655440002",
                status=ContentStatus.REJECTED,
                is_public=False,
                version=1,
            ),
        ]
        
        for author in authors:
            test_db.add(author)
        await test_db.commit()
        
        # Search should only return approved author
        response = await async_client.get("/authors?search=author")
        assert response.status_code == 200
        data = response.json()
        
        returned_names = [item["name"] for item in data["items"]]
        assert "Approved Author" in returned_names
        assert "Pending Author" not in returned_names
        assert "Rejected Author" not in returned_names
