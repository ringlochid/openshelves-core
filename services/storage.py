"""
S3 Storage service for media uploads.
Copied from Auth Service pattern for consistency.
"""

import boto3
from fastapi import HTTPException

from settings import settings


async def get_s3_client():
    """
    Return a boto3 S3 client configured for media uploads.
    Raises HTTPException if required settings are missing.
    """
    if not settings.S3_BUCKET_NAME:
        raise HTTPException(
            status_code=500, detail="S3 media bucket is not configured on the server"
        )
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        endpoint_url=settings.S3_ENDPOINT_URL,
    )
