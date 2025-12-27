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
    # Build client kwargs - supports both explicit credentials (local dev) and IAM roles (AWS)
    client_kwargs = {
        "region_name": settings.AWS_REGION,
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        client_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    if settings.S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

    return boto3.client("s3", **client_kwargs)


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

    # Build client kwargs - only include explicit credentials if provided
    # When running on AWS (App Runner/ECS), IAM roles provide credentials automatically
    client_kwargs = {
        "region_name": settings.AWS_REGION,
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        client_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    if settings.S3_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

    try:
        from botocore.config import Config

        # Set aggressive timeouts to avoid hanging when network is unreachable
        config = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
        client = boto3.client("s3", config=config, **client_kwargs)
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
