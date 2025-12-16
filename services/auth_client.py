"""
Client for Auth Service integration.
Handles trust score adjustments and service-to-service communication.
"""
import logging
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, status

from settings import settings


logger = logging.getLogger(__name__)


class AuthServiceClient:
    """Client for communicating with Auth Service."""
    
    def __init__(self):
        self.base_url = settings.AUTH_SERVICE_URL
        self.service_token = settings.SERVICE_API_KEY
        self.timeout = settings.AUTH_SERVICE_TIMEOUT
        
    def _get_headers(self) -> dict[str, str]:
        """Get headers for service-to-service authentication."""
        return {
            "X-Service-Token": self.service_token,
            "Content-Type": "application/json",
        }
    
    async def adjust_user_trust(
        self,
        user_id: UUID,
        delta: int,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """
        Adjust user's trust score via Auth Service.
        
        Args:
            user_id: User's UUID
            delta: Amount to adjust (+/- integer)
            reason: Human-readable reason for adjustment
            metadata: Optional additional context (entity_type, entity_id, etc.)
            
        Returns:
            Response from Auth Service with new trust score
            
        Raises:
            HTTPException: If Auth Service request fails
        """
        url = f"{self.base_url}/admin/users/{user_id}/trust/adjust"
        payload = {
            "delta": delta,
            "reason": reason,
            "metadata": metadata or {},
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                
                result = response.json()
                logger.info(
                    f"Trust adjusted for user {user_id}: {delta:+d} "
                    f"(reason: {reason}, new_score: {result.get('new_trust_score')})"
                )
                return result
                
        except httpx.TimeoutException:
            logger.error(f"Timeout adjusting trust for user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Auth Service timeout",
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Auth Service returned {e.response.status_code} "
                f"adjusting trust for user {user_id}: {e.response.text}"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Auth Service error: {e.response.text}",
            )
        except Exception as e:
            logger.error(f"Unexpected error adjusting trust for user {user_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to adjust trust score",
            )
    
    async def bulk_adjust_trust(
        self,
        adjustments: list[dict[str, Any]],
    ) -> list[dict]:
        """
        Adjust trust scores for multiple users in batch.
        
        Args:
            adjustments: List of dicts with keys: user_id, delta, reason, metadata
            
        Returns:
            List of results from Auth Service
            
        Note:
            This makes parallel requests for better performance.
            Failed adjustments are logged but don't stop processing.
        """
        results = []
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            tasks = []
            for adjustment in adjustments:
                user_id = adjustment["user_id"]
                url = f"{self.base_url}/admin/users/{user_id}/trust/adjust"
                payload = {
                    "delta": adjustment["delta"],
                    "reason": adjustment["reason"],
                    "metadata": adjustment.get("metadata", {}),
                }
                
                task = client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                )
                tasks.append((user_id, task))
            
            # Execute all requests in parallel
            for user_id, task in tasks:
                try:
                    response = await task
                    response.raise_for_status()
                    results.append(response.json())
                except Exception as e:
                    logger.error(
                        f"Failed to adjust trust for user {user_id} in batch: {str(e)}"
                    )
                    results.append({
                        "user_id": str(user_id),
                        "success": False,
                        "error": str(e),
                    })
        
        logger.info(f"Bulk trust adjustment completed: {len(results)} operations")
        return results
    
    async def health_check(self) -> bool:
        """
        Check if Auth Service is reachable.
        
        Returns:
            True if Auth Service is healthy, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"Auth Service health check failed: {str(e)}")
            return False


# Global instance
auth_service_client = AuthServiceClient()


# Convenience functions for common operations
async def adjust_trust_for_approval(
    user_id: UUID,
    entity_type: str,
    entity_id: int,
    is_book: bool = False,
) -> dict:
    """
    Adjust trust score when content is approved.
    Books get double rewards (20 vs 10).
    
    Args:
        user_id: Content submitter's UUID
        entity_type: "author", "book", or "collection"
        entity_id: Entity's ID
        is_book: Whether this is a book (for doubled reward)
        
    Returns:
        Auth Service response
    """
    delta = 20 if is_book else 10
    reason = f"{entity_type.capitalize()} approved"
    metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": "APPROVED",
    }
    
    return await auth_service_client.adjust_user_trust(
        user_id=user_id,
        delta=delta,
        reason=reason,
        metadata=metadata,
    )


async def adjust_trust_for_rejection(
    user_id: UUID,
    entity_type: str,
    entity_id: int,
    reason: str,
    is_book: bool = False,
) -> dict:
    """
    Adjust trust score when content is rejected.
    Books get double penalties (-10 vs -5).
    
    Args:
        user_id: Content submitter's UUID
        entity_type: "author", "book", or "collection"
        entity_id: Entity's ID
        reason: Rejection reason
        is_book: Whether this is a book (for doubled penalty)
        
    Returns:
        Auth Service response
    """
    delta = -10 if is_book else -5
    reason_text = f"{entity_type.capitalize()} rejected: {reason}"
    metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": "REJECTED",
        "rejection_reason": reason,
    }
    
    return await auth_service_client.adjust_user_trust(
        user_id=user_id,
        delta=delta,
        reason=reason_text,
        metadata=metadata,
    )


async def adjust_trust_for_social_bonus(
    user_id: UUID,
    action: str,
    entity_type: str,
    entity_id: int,
) -> dict:
    """
    Adjust trust score for social engagement (follows/subscriptions).
    +3 per action, capped at +6 per entity by Auth Service.
    
    Args:
        user_id: Content creator's UUID
        action: "follow" or "subscribe"
        entity_type: "author", "book", or "collection"
        entity_id: Entity's ID
        
    Returns:
        Auth Service response
    """
    reason = f"{entity_type.capitalize()} {action}ed"
    metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "bonus_type": "social_engagement",
    }
    
    return await auth_service_client.adjust_user_trust(
        user_id=user_id,
        delta=3,
        reason=reason,
        metadata=metadata,
    )


async def validate_user_exists(user_id: UUID) -> bool:
    """
    Check if a user exists in the Auth Service.
    Used to validate linked_user_id before associating with author.
    
    Args:
        user_id: User's UUID to validate
        
    Returns:
        True if user exists, False otherwise
    """
    try:
        url = f"{auth_service_client.base_url}/users/{user_id}/trust"
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                url,
                headers=auth_service_client._get_headers(),
            )
            return response.status_code == 200
    except Exception as e:
        logger.warning(f"Failed to validate user {user_id}: {str(e)}")
        return False
