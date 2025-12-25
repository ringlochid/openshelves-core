"""
S3 Storage service for media uploads.
Copied from Auth Service pattern for consistency.
"""

import logging

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from fastapi import HTTPException

from settings import settings


logger = logging.getLogger(__name__)


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


async def check_s3_health() -> dict:
    """
    Check S3 connectivity and bucket accessibility.

    Returns:
        Dict with keys:
        - healthy: bool - True if S3 is accessible
        - status: str - "ok", "unhealthy", or "unconfigured"
        - bucket: str | None - Bucket name if configured
        - error: str | None - Error message if unhealthy
    """
    if not settings.S3_BUCKET_NAME:
        return {
            "healthy": False,
            "status": "unconfigured",
            "bucket": None,
            "error": "S3_BUCKET_NAME not configured",
        }

    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        return {
            "healthy": False,
            "status": "unconfigured",
            "bucket": settings.S3_BUCKET_NAME,
            "error": "AWS credentials not configured",
        }

    try:
        client = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
        )
        # head_bucket checks if bucket exists and is accessible
        client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
        return {
            "healthy": True,
            "status": "ok",
            "bucket": settings.S3_BUCKET_NAME,
            "error": None,
        }
    except NoCredentialsError:
        return {
            "healthy": False,
            "status": "unhealthy",
            "bucket": settings.S3_BUCKET_NAME,
            "error": "Invalid AWS credentials",
        }
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        logger.warning(f"S3 health check failed: {error_code} - {error_msg}")
        return {
            "healthy": False,
            "status": "unhealthy",
            "bucket": settings.S3_BUCKET_NAME,
            "error": f"{error_code}: {error_msg}",
        }
    except Exception as e:
        logger.warning(f"S3 health check failed: {str(e)}")
        return {
            "healthy": False,
            "status": "unhealthy",
            "bucket": settings.S3_BUCKET_NAME,
            "error": str(e),
        }
