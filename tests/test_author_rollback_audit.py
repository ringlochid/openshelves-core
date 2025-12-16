"""
Test for author rollback audit trail correctness.

Ensures old_data and new_data in edit history reflect the actual pre/post rollback state.
"""
import pytest
from uuid import uuid4
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Author, ContentStatus, EditHistory
from helpers.jwt_utils import create_test_jwt


@pytest.mark.asyncio
async def test_rollback_audit_captures_correct_pre_post_state(
    async_client: AsyncClient,
    test_db: AsyncSession,
):
    """
    Verify that rollback edit history contains:
    - old_data: state BEFORE rollback (current version)
    - new_data: state AFTER rollback (target version data + updated metadata)
    
    This ensures we can reconstruct what changed during the rollback.
    """
    # Create author with initial state
    user_id = uuid4()
    jwt_token = create_test_jwt(
        user_id=user_id,
        scopes=["authors:draft", "authors:update_own", "authors:rollback_own", "authors:publish_direct"],
        trust_score=60,  # Trusted user to avoid pending status issues
    )
    
    # Version 1: Create author
    create_response = await async_client.post(
        "/authors",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={
            "name": "Original Name",
            "email": "original@example.com",
            "bio": "Original bio",
        }
    )
    assert create_response.status_code == 201
    author_id = create_response.json()["id"]
    
    # Version 2: Update author
    update_response = await async_client.patch(
        f"/authors/{author_id}",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={
            "name": "Updated Name",
            "bio": "Updated bio",
            "version": 1,
        }
    )
    assert update_response.status_code == 200
    v2_data = update_response.json()
    assert v2_data["version"] == 2
    assert v2_data["name"] == "Updated Name"
    
    # Rollback to version 1
    rollback_response = await async_client.post(
        f"/authors/{author_id}/rollback",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={
            "target_version": 1,
            "version": 2,  # Current version
        }
    )
    assert rollback_response.status_code == 200
    v3_data = rollback_response.json()
    assert v3_data["version"] == 3
    assert v3_data["name"] == "Original Name"  # Rolled back
    
    # Check edit history for rollback
    history_result = await test_db.execute(
        select(EditHistory)
        .where(EditHistory.entity_id == author_id)
        .where(EditHistory.version == 3)
    )
    rollback_history = history_result.scalar_one()
    
    # Verify old_data contains PRE-rollback state (version 2 data)
    assert rollback_history.old_data["name"] == "Updated Name"
    assert rollback_history.old_data["bio"] == "Updated bio"
    assert rollback_history.old_data["version"] == 2
    
    # Verify new_data contains POST-rollback state (version 1 data restored + version 3)
    assert rollback_history.new_data["name"] == "Original Name"
    assert rollback_history.new_data["bio"] == "Original bio"
    assert rollback_history.new_data["version"] == 3
    
    # Verify changes reflect the actual diff
    modified_fields = [change["field"] for change in rollback_history.changes["modified"]]
    assert "name" in modified_fields
    assert "bio" in modified_fields
