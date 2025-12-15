"""
Unit tests for edit_history helper functions.
Tests version conflict detection, change calculation, and history recording.
"""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timezone
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from helpers.edit_history import (
    check_version_conflict,
    format_version_error,
    calculate_changes,
    record_edit,
    record_create,
    record_update,
    record_delete,
    serialize_entity,
)
from models import EditHistory, EditAction


class TestVersionConflict:
    """Test version conflict detection."""
    
    def test_check_version_conflict_match(self):
        """Should not raise when versions match."""
        # Should complete without exception
        check_version_conflict(5, 5, "book", 123)
    
    def test_check_version_conflict_mismatch(self):
        """Should raise HTTPException 409 when versions don't match."""
        with pytest.raises(HTTPException) as exc_info:
            check_version_conflict(5, 3, "book", 123)
        
        assert exc_info.value.status_code == 409
        assert "version_conflict" in str(exc_info.value.detail)
    
    def test_format_version_error(self):
        """Should format version error correctly."""
        error = format_version_error("author", 456, 10, 8)
        
        assert error["error"] == "version_conflict"
        assert error["entity_type"] == "author"
        assert error["entity_id"] == 456
        assert error["current_version"] == 10
        assert error["requested_version"] == 8
        assert "resolution" in error


class TestChangeCalculation:
    """Test change calculation logic."""
    
    def test_calculate_changes_empty(self):
        """Should handle empty/None inputs."""
        result = calculate_changes(None, None)
        
        assert result["added"] == []
        assert result["removed"] == []
        assert result["modified"] == []
        assert result["total_changes"] == 0
    
    def test_calculate_changes_added_fields(self):
        """Should detect added fields."""
        old = {"name": "John"}
        new = {"name": "John", "email": "john@example.com", "age": 30}
        
        result = calculate_changes(old, new)
        
        assert set(result["added"]) == {"email", "age"}
        assert result["removed"] == []
        assert result["total_changes"] == 2
    
    def test_calculate_changes_removed_fields(self):
        """Should detect removed fields."""
        old = {"name": "John", "email": "john@example.com", "age": 30}
        new = {"name": "John"}
        
        result = calculate_changes(old, new)
        
        assert result["added"] == []
        assert set(result["removed"]) == {"email", "age"}
        assert result["total_changes"] == 2
    
    def test_calculate_changes_modified_fields(self):
        """Should detect modified fields."""
        old = {"name": "John", "email": "john@old.com"}
        new = {"name": "Jane", "email": "jane@new.com"}
        
        result = calculate_changes(old, new)
        
        assert result["added"] == []
        assert result["removed"] == []
        assert len(result["modified"]) == 2
        
        # Check structure
        name_change = next(m for m in result["modified"] if m["field"] == "name")
        assert name_change["old_value"] == "John"
        assert name_change["new_value"] == "Jane"
    
    def test_calculate_changes_complex(self):
        """Should handle complex changes with add, remove, and modify."""
        old = {
            "title": "Old Title",
            "year": 2020,
            "isbn": "123",
            "genre": "fiction"
        }
        new = {
            "title": "New Title",
            "year": 2020,
            "author": "John Doe",
            "publisher": "ABC"
        }
        
        result = calculate_changes(old, new)
        
        assert set(result["added"]) == {"author", "publisher"}
        assert set(result["removed"]) == {"isbn", "genre"}
        assert len(result["modified"]) == 1
        assert result["modified"][0]["field"] == "title"
        assert result["total_changes"] == 5


class TestRecordEdit:
    """Test edit recording functions."""
    
    @pytest.mark.asyncio
    async def test_record_edit_create(self):
        """Should record a CREATE action."""
        db = AsyncMock()
        user_id = uuid4()
        data = {"title": "Test Book", "year": 2023}
        
        history = await record_edit(
            db=db,
            entity_type="book",
            entity_id=123,
            action=EditAction.CREATE,
            user_id=user_id,
            old_data=None,
            new_data=data,
            version=1,
            parent_version=None
        )
        
        assert isinstance(history, EditHistory)
        assert history.entity_type == "book"
        assert history.entity_id == 123
        assert history.action == EditAction.CREATE
        assert history.user_id == user_id
        assert history.version == 1
        assert history.parent_version is None
        assert history.old_data is None
        assert history.new_data == data
        assert history.changes["total_changes"] == 2  # title and year added
        
        db.add.assert_called_once()
        db.flush.assert_awaited_once()
    
    @pytest.mark.asyncio
    async def test_record_create_convenience(self):
        """Should use convenience function for CREATE."""
        db = AsyncMock()
        user_id = uuid4()
        data = {"name": "Test Author"}
        
        history = await record_create(
            db=db,
            entity_type="author",
            entity_id=456,
            user_id=user_id,
            data=data
        )
        
        assert history.action == EditAction.CREATE
        assert history.version == 1
        assert history.parent_version is None
    
    @pytest.mark.asyncio
    async def test_record_update_convenience(self):
        """Should use convenience function for UPDATE."""
        db = AsyncMock()
        user_id = uuid4()
        old_data = {"title": "Old Title", "year": 2020}
        new_data = {"title": "New Title", "year": 2023}
        
        history = await record_update(
            db=db,
            entity_type="book",
            entity_id=789,
            user_id=user_id,
            old_data=old_data,
            new_data=new_data,
            new_version=3,
            old_version=2
        )
        
        assert history.action == EditAction.UPDATE
        assert history.version == 3
        assert history.parent_version == 2
        assert len(history.changes["modified"]) == 2
    
    @pytest.mark.asyncio
    async def test_record_delete_convenience(self):
        """Should use convenience function for DELETE."""
        db = AsyncMock()
        user_id = uuid4()
        data = {"title": "Deleted Book", "year": 2020}
        
        history = await record_delete(
            db=db,
            entity_type="book",
            entity_id=999,
            user_id=user_id,
            data=data,
            version=5
        )
        
        assert history.action == EditAction.DELETE
        assert history.version == 6  # version + 1
        assert history.parent_version == 5
        assert history.new_data is None


class TestSerializeEntity:
    """Test entity serialization."""
    
    def test_serialize_entity_basic(self):
        """Should serialize SQLAlchemy model to dict."""
        # Create a mock entity
        entity = MagicMock()
        entity.__table__ = MagicMock()
        
        # Mock columns
        col1 = MagicMock()
        col1.name = "id"
        col2 = MagicMock()
        col2.name = "title"
        col3 = MagicMock()
        col3.name = "created_at"
        
        entity.__table__.columns = [col1, col2, col3]
        entity.id = 123
        entity.title = "Test Book"
        entity.created_at = datetime(2023, 1, 1, tzinfo=timezone.utc)
        
        result = serialize_entity(entity)
        
        assert result["id"] == 123
        assert result["title"] == "Test Book"
        assert "created_at" not in result  # Excluded by default
    
    def test_serialize_entity_uuid_conversion(self):
        """Should convert UUIDs to strings."""
        entity = MagicMock()
        entity.__table__ = MagicMock()
        
        col = MagicMock()
        col.name = "user_id"
        entity.__table__.columns = [col]
        
        test_uuid = uuid4()
        entity.user_id = test_uuid
        
        result = serialize_entity(entity)
        
        assert result["user_id"] == str(test_uuid)
        assert isinstance(result["user_id"], str)
    
    def test_serialize_entity_datetime_conversion(self):
        """Should convert datetime to ISO format."""
        entity = MagicMock()
        entity.__table__ = MagicMock()
        
        col = MagicMock()
        col.name = "published_at"
        entity.__table__.columns = [col]
        
        dt = datetime(2023, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        entity.published_at = dt
        
        result = serialize_entity(entity, exclude_fields=set())
        
        assert result["published_at"] == dt.isoformat()
    
    def test_serialize_entity_custom_exclusion(self):
        """Should respect custom exclude fields."""
        entity = MagicMock()
        entity.__table__ = MagicMock()
        
        col1 = MagicMock()
        col1.name = "id"
        col2 = MagicMock()
        col2.name = "secret"
        col3 = MagicMock()
        col3.name = "name"
        
        entity.__table__.columns = [col1, col2, col3]
        entity.id = 1
        entity.secret = "confidential"
        entity.name = "Public Name"
        
        result = serialize_entity(entity, exclude_fields={"secret"})
        
        assert result["id"] == 1
        assert result["name"] == "Public Name"
        assert "secret" not in result
