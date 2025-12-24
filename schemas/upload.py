"""
Upload schemas for media upload workflow.
Follows the presign → upload → commit pattern.
"""

from datetime import datetime
from pydantic import BaseModel, Field
from uuid import UUID


# ========================================
# BASE REQUEST
# ========================================


class BaseUploadRequest(BaseModel):
    """Base fields for all upload requests."""

    content_type: str = Field(..., description="MIME type of the file")


# ========================================
# ENTITY-SPECIFIC UPLOAD REQUESTS
# ========================================


class BookCoverUploadRequest(BaseUploadRequest):
    """Request to upload a book cover."""

    pass


class BookFileUploadRequest(BaseUploadRequest):
    """Request to upload a book file (PDF/EPUB)."""

    filename: str = Field(..., description="Original filename with extension")


class AuthorAvatarUploadRequest(BaseUploadRequest):
    """Request to upload an author avatar."""

    pass


class CollectionCoverUploadRequest(BaseUploadRequest):
    """Request to upload a collection cover."""

    pass


# ========================================
# UPLOAD RESPONSE (Shared)
# ========================================


class UploadResponse(BaseModel):
    """Response containing presigned upload URL and metadata."""

    upload_id: UUID = Field(..., description="Unique ID for this upload")
    s3_key: str = Field(..., description="S3 key where file will be stored")
    url: str = Field(..., description="Presigned POST URL")
    fields: dict = Field(..., description="Additional fields required for the POST")
    expires_at: datetime = Field(..., description="When the presigned URL expires")


# ========================================
# COMMIT REQUEST
# ========================================


class CommitRequest(BaseModel):
    """Request to commit an upload after file has been uploaded."""

    upload_id: UUID = Field(..., description="The upload_id from presign response")
    s3_key: str = Field(..., description="The s3_key from presign response")
