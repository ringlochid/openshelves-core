"""
JWT test utility for creating test tokens using RS256 (matches production).
"""
import jwt
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone
from typing import List
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from settings import settings


# Test algorithm (matches production RS256)
TEST_JWT_ALGORITHM = "RS256"

# Generate test RSA key pair (2048-bit)
_test_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Serialize to PEM format
TEST_JWT_PRIVATE_KEY = _test_private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
).decode()

TEST_JWT_PUBLIC_KEY = _test_private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


def create_test_jwt(
    user_id: UUID,
    scopes: List[str],
    trust_score: int = 0,
    reputation_score: float = 100.0,
    expires_in_minutes: int = 60,
) -> str:
    """
    Create a test JWT token with specified user_id, scopes, and trust score.
    Uses RS256 algorithm to match production Auth Service.
    
    Args:
        user_id: The user's UUID
        scopes: List of permission scopes (e.g., ["jury:vote", "authors:create"])
        trust_score: User's trust score (0-100)
        reputation_score: User's reputation percentage (0-100)
        expires_in_minutes: Token expiration time in minutes
    
    Returns:
        JWT token string
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),  # Standard JWT claim
        "user_id": str(user_id),  # For backward compatibility
        "type": "access",  # Token type (required by auth dependency)
        "roles": ["user"],  # Default role
        "scopes": scopes,
        "trust_score": trust_score,
        "reputation_percentage": reputation_score,
        "jti": str(uuid4()),  # JWT ID for blacklist checking
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_in_minutes)).timestamp()),
    }
    
    token = jwt.encode(payload, TEST_JWT_PRIVATE_KEY, algorithm=TEST_JWT_ALGORITHM)
    return token


def decode_test_jwt(token: str) -> dict:
    """
    Decode a test JWT token.
    
    Args:
        token: JWT token string
    
    Returns:
        Decoded payload dictionary
    """
    return jwt.decode(
        token, 
        TEST_JWT_PUBLIC_KEY, 
        algorithms=[TEST_JWT_ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER
    )
