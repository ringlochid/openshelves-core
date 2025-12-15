"""
Unit tests for JWT authentication and authorization.
Tests token validation, scope checks, role checks, and trust requirements.
"""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from jose import jwt
from fastapi import HTTPException

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dependencies.auth import (
    decode_and_validate_jwt,
    get_current_user,
    require_scope,
    require_role,
    require_min_trust,
)
from settings import settings


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
    monkeypatch.setattr("dependencies.auth.jwt_public_key", public_key)
    
    # Encode token
    token = jwt.encode(valid_token_payload, private_key, algorithm="RS256")
    
    # Decode and validate
    result = decode_and_validate_jwt(token)
    
    assert result["user_id"] == valid_token_payload["sub"]
    assert result["roles"] == ["user"]
    assert result["scopes"] == ["read:books", "write:reviews"]
    assert result["trust_score"] == 75


def test_decode_expired_token(valid_token_payload, private_key, public_key, monkeypatch):
    """Test expired token raises HTTPException."""
    monkeypatch.setattr("dependencies.auth.jwt_public_key", public_key)
    
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
    
    monkeypatch.setattr("dependencies.auth.jwt_public_key", wrong_key)
    
    with pytest.raises(HTTPException) as exc_info:
        decode_and_validate_jwt(token)
    
    assert exc_info.value.status_code == 401


def test_decode_wrong_audience(valid_token_payload, private_key, public_key, monkeypatch):
    """Test wrong audience raises HTTPException."""
    monkeypatch.setattr("dependencies.auth.jwt_public_key", public_key)
    
    valid_token_payload["aud"] = "wrong-audience"
    token = jwt.encode(valid_token_payload, private_key, algorithm="RS256")
    
    with pytest.raises(HTTPException) as exc_info:
        decode_and_validate_jwt(token)
    
    assert exc_info.value.status_code == 401


def test_require_scope_success(valid_token_payload):
    """Test scope requirement passes with correct scope."""
    checker = require_scope("read:books")
    
    # Should not raise exception
    checker(valid_token_payload)


def test_require_scope_failure(valid_token_payload):
    """Test scope requirement fails without scope."""
    checker = require_scope("admin:users")
    
    with pytest.raises(HTTPException) as exc_info:
        checker(valid_token_payload)
    
    assert exc_info.value.status_code == 403
    assert "insufficient permissions" in str(exc_info.value.detail).lower()


def test_require_role_success(valid_token_payload):
    """Test role requirement passes with correct role."""
    checker = require_role("user")
    
    # Should not raise exception
    checker(valid_token_payload)


def test_require_role_failure(valid_token_payload):
    """Test role requirement fails without role."""
    checker = require_role("admin")
    
    with pytest.raises(HTTPException) as exc_info:
        checker(valid_token_payload)
    
    assert exc_info.value.status_code == 403


def test_require_min_trust_success(valid_token_payload):
    """Test trust requirement passes with sufficient trust."""
    checker = require_min_trust(50)
    
    # Should not raise exception (trust_score is 75)
    checker(valid_token_payload)


def test_require_min_trust_failure(valid_token_payload):
    """Test trust requirement fails with insufficient trust."""
    checker = require_min_trust(100)
    
    with pytest.raises(HTTPException) as exc_info:
        checker(valid_token_payload)
    
    assert exc_info.value.status_code == 403
    assert "trust" in str(exc_info.value.detail).lower()


def test_multiple_scopes(valid_token_payload):
    """Test requiring multiple scopes."""
    checker = require_scope("read:books", "write:reviews")
    
    # Should pass with both scopes
    checker(valid_token_payload)
    
    # Should fail missing one scope
    checker_fail = require_scope("read:books", "admin:books")
    with pytest.raises(HTTPException):
        checker_fail(valid_token_payload)


def test_missing_trust_score(valid_token_payload):
    """Test handling missing trust_score in token."""
    del valid_token_payload["trust_score"]
    
    checker = require_min_trust(50)
    
    with pytest.raises(HTTPException) as exc_info:
        checker(valid_token_payload)
    
    assert exc_info.value.status_code == 403
