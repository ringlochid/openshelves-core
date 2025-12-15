"""
Edit History Helper Functions for Library Service.
Provides utilities for version tracking, change recording, and conflict detection.
"""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from models import EditHistory, EditAction


# ========================================
# Version Conflict Detection
# ========================================

def check_version_conflict(current_version: int, request_version: int, entity_type: str, entity_id: int) -> None:
    """
    Check for optimistic locking conflicts.
    
    Raises HTTPException 409 if versions don't match.
    """
    if current_version != request_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=format_version_error(entity_type, entity_id, current_version, request_version)
        )


def format_version_error(entity_type: str, entity_id: int, current: int, requested: int) -> dict[str, Any]:
    """Format version conflict error response."""
    return {
        "error": "version_conflict",
        "message": f"{entity_type.capitalize()} has been modified by another user",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "current_version": current,
        "requested_version": requested,
        "resolution": "Refresh the entity and reapply your changes"
    }


# ========================================
# Change Calculation
# ========================================

def calculate_changes(old_data: dict[str, Any] | None, new_data: dict[str, Any] | None) -> dict[str, Any]:
    """
    Calculate structured diff between two states.
    
    Returns dict with:
    - added: list of new field names
    - removed: list of deleted field names
    - modified: list of {field, old_value, new_value} for changed fields
    """
    if old_data is None:
        old_data = {}
    if new_data is None:
        new_data = {}
    
    old_keys = set(old_data.keys())
    new_keys = set(new_data.keys())
    
    added = list(new_keys - old_keys)
    removed = list(old_keys - new_keys)
    
    modified = []
    for key in old_keys & new_keys:
        if old_data[key] != new_data[key]:
            modified.append({
                "field": key,
                "old_value": old_data[key],
                "new_value": new_data[key]
            })
    
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "total_changes": len(added) + len(removed) + len(modified)
    }


# ========================================
# Edit Recording
# ========================================

async def record_edit(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    action: EditAction,
    user_id: UUID,
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any] | None,
    version: int,
    parent_version: int | None = None
) -> EditHistory:
    """
    Create an edit history record with calculated changes.
    
    Args:
        db: Async database session
        entity_type: Type of entity (author, book, review, collection)
        entity_id: Entity ID
        action: Edit action type
        user_id: User who made the change
        old_data: Previous state (None for CREATE)
        new_data: New state (None for DELETE)
        version: New version number
        parent_version: Previous version number (None for CREATE)
    
    Returns:
        Created EditHistory instance
    """
    # Calculate changes
    changes = calculate_changes(old_data, new_data)
    
    # Create history record
    history = EditHistory(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        user_id=user_id,
        version=version,
        parent_version=parent_version,
        old_data=old_data,
        new_data=new_data,
        changes=changes,
        created_at=datetime.now(timezone.utc)
    )
    
    db.add(history)
    await db.flush()  # Get ID without committing
    
    return history


async def record_create(db: AsyncSession, entity_type: str, entity_id: int, user_id: UUID, data: dict[str, Any]) -> EditHistory:
    """Convenience function for recording creation."""
    return await record_edit(
        db=db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=EditAction.CREATE,
        user_id=user_id,
        old_data=None,
        new_data=data,
        version=1,
        parent_version=None
    )


async def record_update(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    user_id: UUID,
    old_data: dict[str, Any],
    new_data: dict[str, Any],
    new_version: int,
    old_version: int
) -> EditHistory:
    """Convenience function for recording updates."""
    return await record_edit(
        db=db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=EditAction.UPDATE,
        user_id=user_id,
        old_data=old_data,
        new_data=new_data,
        version=new_version,
        parent_version=old_version
    )


async def record_delete(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    user_id: UUID,
    data: dict[str, Any],
    version: int
) -> EditHistory:
    """Convenience function for recording soft deletes."""
    return await record_edit(
        db=db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=EditAction.DELETE,
        user_id=user_id,
        old_data=data,
        new_data=None,
        version=version + 1,
        parent_version=version
    )


async def record_approval(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    user_id: UUID,
    old_data: dict[str, Any],
    new_data: dict[str, Any],
    new_version: int,
    old_version: int
) -> EditHistory:
    """Convenience function for recording approvals."""
    return await record_edit(
        db=db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=EditAction.APPROVE,
        user_id=user_id,
        old_data=old_data,
        new_data=new_data,
        version=new_version,
        parent_version=old_version
    )


async def record_rejection(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    user_id: UUID,
    old_data: dict[str, Any],
    new_data: dict[str, Any],
    new_version: int,
    old_version: int
) -> EditHistory:
    """Convenience function for recording rejections."""
    return await record_edit(
        db=db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=EditAction.REJECT,
        user_id=user_id,
        old_data=old_data,
        new_data=new_data,
        version=new_version,
        parent_version=old_version
    )


async def record_recovery(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    user_id: UUID,
    old_data: dict[str, Any],
    new_data: dict[str, Any],
    new_version: int,
    old_version: int
) -> EditHistory:
    """Convenience function for recording soft delete recovery."""
    return await record_edit(
        db=db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=EditAction.RECOVER,
        user_id=user_id,
        old_data=old_data,
        new_data=new_data,
        version=new_version,
        parent_version=old_version
    )


# ========================================
# Entity Data Serialization
# ========================================

def serialize_entity(entity: Any, exclude_fields: set[str] | None = None) -> dict[str, Any]:
    """
    Serialize SQLAlchemy model to dict for history storage.
    
    Args:
        entity: SQLAlchemy model instance
        exclude_fields: Fields to exclude from serialization
    
    Returns:
        Dict representation suitable for JSON storage
    """
    if exclude_fields is None:
        exclude_fields = {"created_at", "updated_at"}  # Always exclude timestamps
    
    data = {}
    for column in entity.__table__.columns:
        if column.name not in exclude_fields:
            value = getattr(entity, column.name)
            
            # Convert UUIDs and datetime to strings
            if isinstance(value, UUID):
                data[column.name] = str(value)
            elif isinstance(value, datetime):
                data[column.name] = value.isoformat()
            else:
                data[column.name] = value
    
    return data
