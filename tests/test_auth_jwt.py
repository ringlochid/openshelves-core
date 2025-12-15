"""
Unit tests for JWT authentication and authorization.
Tests token validation, scope checks, role checks, and trust requirements.
"""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from jose import jwt
from fastapi import HTTPException
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dependencies.auth import (
    decode_and_validate_jwt,
    require_scope,
    require_role,
    require_min_trust,
)
from settings import settings


@pytest.fixture
def valid_user_dict():
    """Valid user dict from decoded JWT (as returned by get_current_user)."""
    return {
        "user_id": uuid4(),
        "roles": ["user"],
        "scopes": ["read:books", "write:reviews"],
        "trust_score": 75,
        "reputation_percentage": 85.5,
        "jti": str(uuid4()),
    }


@pytest.fixture
def valid_token_payload():
    """Valid JWT payload fixture."""
    return {
        "sub": str(uuid4()),
        "type": "access",
        "roles": ["user"],
        "scopes": ["read:books", "write:reviews"],
        "trust_score": 75,
        "reputation_percentage": 85.5,
        "jti": str(uuid4()),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }


@pytest.fixture
def private_key():
    """Generate test RSA private key."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    return pem.decode()


@pytest.fixture
def public_key(private_key):
    """Extract public key from private key."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives import serialization
    
    key = load_pem_private_key(private_key.encode(), password=None)
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pub.decode()


def test_decode_valid_token(valid_token_payload, private_key, public_key, monkeypatch):
    """Test decoding a valid JWT token."""
    # Mock public key loading
    monkeypatch.setattr("dependencies.auth._jwt_public_key", public_key)
    
    # Encode token
    token = jwt.encode(valid_token_payload, private_key, algorithm="RS256")
    
    # Decode and validate
    result = decode_and_validate_jwt(token)
    
    assert result["sub"] == valid_token_payload["sub"]
    assert result["roles"] == ["user"]
    assert result["scopes"] == ["read:books", "write:reviews"]
    assert result["trust_score"] == 75


def test_decode_expired_token(valid_token_payload, private_key, public_key, monkeypatch):
    """Test expired token raises HTTPException."""
    monkeypatch.setattr("dependencies.auth._jwt_public_key", public_key)
    
    # Create expired token
    valid_token_payload["exp"] = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode(valid_token_payload, private_key, algorithm="RS256")
    
    with pytest.raises(HTTPException) as exc_info:
        decode_and_validate_jwt(token)
    
    assert exc_info.value.status_code == 401
    assert "expired" in str(exc_info.value.detail).lower()


def test_decode_invalid_signature(valid_token_payload, monkeypatch):
    """Test invalid signature raises HTTPException."""
    # Use wrong key
    wrong_key = "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----"
    token = "invalid.token.signature"
    
    monkeypatch.setattr("dependencies.auth._jwt_public_key", wrong_key)
    
    with pytest.raises(HTTPException) as exc_info:
        decode_and_validate_jwt(token)
    
    assert exc_info.value.status_code == 401


def test_decode_wrong_audience(valid_token_payload, private_key, public_key, monkeypatch):
    """Test wrong audience raises HTTPException."""
    monkeypatch.setattr("dependencies.auth._jwt_public_key", public_key)
    
    valid_token_payload["aud"] = "wrong-audience"
    token = jwt.encode(valid_token_payload, private_key, algorithm="RS256")
    
    with pytest.raises(HTTPException) as exc_info:
        decode_and_validate_jwt(token)
    
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_scope_success(valid_user_dict):
    """Test scope requirement passes with correct scope."""
    checker = require_scope("read:books")
    
    # Should not raise exception
    result = await checker(valid_user_dict)
    assert result == valid_user_dict


@pytest.mark.asyncio
async def test_require_scope_failure(valid_user_dict):
    """Test scope requirement fails without scope."""
    checker = require_scope("admin:users")
    
    with pytest.raises(HTTPException) as exc_info:
        await checker(valid_user_dict)
    
    assert exc_info.value.status_code == 403
    assert "missing" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_require_role_success(valid_user_dict):
    """Test role requirement passes with correct role."""
    checker = require_role("user")
    
    # Should not raise exception
    result = await checker(valid_user_dict)
    assert result == valid_user_dict


@pytest.mark.asyncio
async def test_require_role_failure(valid_user_dict):
    """Test role requirement fails without role."""
    checker = require_role("admin")
    
    with pytest.raises(HTTPException) as exc_info:
        await checker(valid_user_dict)
    
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_min_trust_success(valid_user_dict):
    """Test trust requirement passes with sufficient trust."""
    checker = require_min_trust(50)
    
    # Should not raise exception (trust_score is 75)
    result = await checker(valid_user_dict)
    assert result == valid_user_dict


@pytest.mark.asyncio
async def test_require_min_trust_failure(valid_user_dict):
    """Test trust requirement fails with insufficient trust."""
    checker = require_min_trust(100)
    
    with pytest.raises(HTTPException) as exc_info:
        await checker(valid_user_dict)
    
    assert exc_info.value.status_code == 403
    assert "trust" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_multiple_scopes(valid_user_dict):
    """Test requiring multiple scopes."""
    checker = require_scope("read:books", "write:reviews")
    
    # Should pass with both scopes
    result = await checker(valid_user_dict)
    assert result == valid_user_dict
    
    # Should fail missing one scope
    checker_fail = require_scope("read:books", "admin:books")
    with pytest.raises(HTTPException):
        await checker_fail(valid_user_dict)


@pytest.mark.asyncio
async def test_missing_trust_score(valid_user_dict):
    """Test handling missing trust_score in token."""
    user_no_trust = valid_user_dict.copy()
    user_no_trust["trust_score"] = 0  # Default to 0 if missing
    
    checker = require_min_trust(50)
    
    with pytest.raises(HTTPException) as exc_info:
        await checker(user_no_trust)
    
    assert exc_info.value.status_code == 403
