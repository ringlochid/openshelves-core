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
    """Complete edit history record for detail view."""

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
    action: EditAction
    user_id: UUID
    version: int
    created_at: datetime
    # Change counts for quick display
    added_count: int = 0
    modified_count: int = 0
    removed_count: int = 0


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
