"""
Authentication and authorization dependencies for Library Service.
Validates JWTs issued by Auth Service.
"""

from pathlib import Path
from typing import Callable
from uuid import UUID

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from settings import settings
from cache import check_access_in_blacklist, init_redis


# Global variable to store loaded JWT public key
_jwt_public_key: str | None = None

security = HTTPBearer()


def load_jwt_public_key() -> None:
    """Load JWT public key from file at application startup."""
    global _jwt_public_key
    key_path = Path(settings.JWT_PUBLIC_KEY_PATH)
    if not key_path.exists():
        raise FileNotFoundError(
            f"JWT public key not found at {settings.JWT_PUBLIC_KEY_PATH}"
        )
    _jwt_public_key = key_path.read_text()


def get_jwt_public_key() -> str:
    """Get the loaded JWT public key."""
    if _jwt_public_key is None:
        raise RuntimeError(
            "JWT public key not loaded. Call load_jwt_public_key() at startup."
        )
    return _jwt_public_key


def decode_and_validate_jwt(token: str) -> dict:
    """
    Decode and validate JWT access token.

    Args:
        token: JWT token string

    Returns:
        Decoded payload dictionary

    Raises:
        HTTPException: If token is invalid, expired, or wrong type
    """
    try:
        public_key = get_jwt_public_key()
        payload = jwt.decode(
            token,
            public_key,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify token type is "access"
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type. Expected access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("iss") != settings.JWT_ISSUER:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong issuer.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("aud") != settings.JWT_AUDIENCE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong audience.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Extract and validate current user from JWT token.

    Returns:
        Dictionary with user information:
        - user_id (UUID): User ID
        - roles (list[str]): User roles (e.g., ["user"], ["admin"])
        - scopes (list[str]): User scopes/permissions
        - trust_score (int): User's trust score
        - reputation_percentage (float): Reputation modifier
        - jti (str): JWT ID for blacklist checking

    Raises:
        HTTPException: If token is invalid or user is unauthorized
    """
    token = credentials.credentials
    payload = decode_and_validate_jwt(token)

    # Check if token is blacklisted (shared Redis with Auth Service)
    jti = payload.get("jti")
    if jti:
        r = await init_redis()
        if await check_access_in_blacklist(jti, r):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Extract user information
    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid user ID in token: {str(e)}",
        )

    roles = payload.get("roles", [])
    if not roles or "unverified" in roles or "blacklisted" in roles:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is unverified or blacklisted",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "user_id": user_id,  # UUID object
        "roles": roles,
        "scopes": payload.get("scopes", []),
        "trust_score": payload.get("trust_score", 0),
        "reputation_percentage": payload.get("reputation_percentage", 100.0),
        "jti": jti,
    }


# Optional security - doesn't require auth header
security_optional = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_optional),
) -> dict | None:
    """
    Extract and validate current user from JWT token, or return None for anonymous users.

    Unlike get_current_user, this does NOT raise errors for missing/invalid tokens.
    Use this for endpoints that allow both authenticated and anonymous access.

    Returns:
        Dictionary with user information (same as get_current_user), or None if:
        - No auth header provided
        - Token is invalid/expired
        - Token is blacklisted
    """
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        payload = decode_and_validate_jwt(token)

        # Check if token is blacklisted
        jti = payload.get("jti")
        if jti:
            r = await init_redis()
            if await check_access_in_blacklist(jti, r):
                return None

        # Extract user information
        user_id = UUID(payload["sub"])

        roles = payload.get("roles", [])
        if not roles or "unverified" in roles or "blacklisted" in roles:
            raise ValueError("User is unverified or blacklisted")

        return {
            "user_id": user_id,  # UUID object (consistent with get_current_user)
            "roles": roles,
            "scopes": payload.get("scopes", []),
            "trust_score": payload.get("trust_score", 0),
            "reputation_percentage": payload.get("reputation_percentage", 100.0),
            "jti": jti,
        }
    except (HTTPException, KeyError, ValueError):
        # Any error means treat as anonymous
        return None


def require_scope(*required_scopes: str) -> Callable:
    """
    Dependency factory to require specific scopes.

    Usage:
        @app.post("/authors", dependencies=[Depends(require_scope("content:submit"))])

    Args:
        *required_scopes: One or more required scopes

    Returns:
        Dependency function that checks scopes
    """

    async def check_scopes(user: dict = Depends(get_current_user)) -> dict:
        user_scopes = set(user.get("scopes", []))
        missing_scopes = set(required_scopes) - user_scopes

        if missing_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scopes: {', '.join(missing_scopes)}",
            )

        return user

    return check_scopes


def require_role(*required_roles: str) -> Callable:
    """
    Dependency factory to require specific roles.

    Usage:
        @app.get("/admin/authors", dependencies=[Depends(require_role("admin"))])

    Args:
        *required_roles: One or more required roles

    Returns:
        Dependency function that checks roles
    """

    async def check_roles(user: dict = Depends(get_current_user)) -> dict:
        user_roles = set(user.get("roles", []))

        # Check if user has any of the required roles
        if not any(role in user_roles for role in required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of these roles: {', '.join(required_roles)}",
            )

        return user

    return check_roles


async def verify_service_token(
    x_service_token: str | None = Header(None, alias="X-Service-Token"),
) -> None:
    """
    Verify service-to-service authentication token.

    Used for Auth Service to call Library Service endpoints.

    Args:
        x_service_token: Service API key from header

    Raises:
        HTTPException: If service token is missing or invalid
    """
    if not x_service_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Service-Token header",
        )

    if x_service_token != settings.SERVICE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )


def require_min_trust(min_trust: int) -> Callable:
    """
    Dependency factory to require minimum trust score.

    Usage:
        @app.post("/reviews/{id}/vote", dependencies=[Depends(require_min_trust(50))])

    Args:
        min_trust: Minimum required trust score

    Returns:
        Dependency function that checks trust score
    """

    async def check_trust(user: dict = Depends(get_current_user)) -> dict:
        user_trust = user.get("trust_score", 0)

        if user_trust < min_trust:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires trust score of at least {min_trust} (you have {user_trust})",
            )

        return user

    return check_trust
