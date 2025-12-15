"""
Edit History Pydantic schemas for Library Service.
Supports complete audit trail with version tracking and change diffs.
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from models import EditAction
from .shared import BaseSchema


# ========================================
# Response Schemas
# ========================================

class EditHistoryRead(BaseSchema):
    """Complete edit history record."""
    id: int
    entity_type: str  # author, book, review, collection
    entity_id: int
    action: EditAction  # CREATE, UPDATE, DELETE, APPROVE, REJECT, RECOVER
    user_id: UUID
    version: int
    parent_version: int | None
    old_data: dict[str, Any] | None = Field(None, description="Previous state as JSON")
    new_data: dict[str, Any] | None = Field(None, description="New state as JSON")
    changes: dict[str, Any] | None = Field(None, description="Structured diff summary")
    created_at: datetime


class EditHistorySummary(BaseSchema):
    """Condensed history record for list views (without full JSON data)."""
    id: int
    entity_type: str
    entity_id: int
    action: EditAction
    user_id: UUID
    version: int
    created_at: datetime


# ========================================
# Change Formatting
# ========================================

class FieldChange(BaseModel):
    """Single field change detail."""
    field: str
    old_value: Any | None
    new_value: Any | None
    change_type: str  # added, removed, modified


class ChangesSummary(BaseModel):
    """Human-readable summary of changes."""
    fields_added: list[str] = Field(default_factory=list)
    fields_removed: list[str] = Field(default_factory=list)
    fields_modified: list[FieldChange] = Field(default_factory=list)
    total_changes: int


# ========================================
# Query Schemas
# ========================================

class EditHistoryQuery(BaseModel):
    """Query parameters for edit history."""
    entity_type: str | None = Field(None, pattern="^(author|book|review|collection)$")
    entity_id: int | None = None
    user_id: UUID | None = None
    action: EditAction | None = None
    version: int | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


# ========================================
# Pagination Response
# ========================================

class EditHistoryListResponse(BaseModel):
    """Paginated list of edit history records."""
    items: list[EditHistorySummary]
    total: int
    page: int
    per_page: int
    pages: int


class EditHistoryDetailResponse(BaseModel):
    """Single history record with parsed changes."""
    record: EditHistoryRead
    changes_summary: ChangesSummary


# ========================================
# Rollback Schema
# ========================================

class RollbackRequest(BaseModel):
    """Request to rollback to a specific version."""
    target_version: int = Field(..., description="Version to rollback to")
    reason: str = Field(..., min_length=1, description="Reason for rollback")
