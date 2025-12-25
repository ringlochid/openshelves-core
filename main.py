from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from routers import author, jury, book, collection, upload, history
from cache import close_redis, init_redis
from database import AsyncSessionLocal
from dependencies.auth import load_jwt_public_key
from settings import settings


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Load JWT public key for token validation
    try:
        load_jwt_public_key()
        logger.info("✓ JWT public key loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load JWT public key: {e}")
        raise

    # Initialize Redis connection
    app.state.redis = await init_redis()
    logger.info("✓ Redis connection initialized")

    try:
        yield
    finally:
        # Cleanup Redis connection
        await close_redis()
        logger.info("✓ Redis connection closed")


app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_meta(request: Request, call_next):
    # Check X-Forwarded-For for AWS ALB/proxy deployments
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else None
    request.state.meta = {"ip": ip}
    response = await call_next(request)
    return response


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Liveness probe - always returns 200 OK.
    """
    return {"status": "ok"}


@app.get("/test", tags=["Test"], response_class=FileResponse)
async def serve_test_frontend():
    """
    Serve the Auth Tester frontend for interactive API testing.
    """
    return FileResponse("frontend-test/index.html", media_type="text/html")


@app.get("/ready", tags=["Health"])
async def readiness_check():
    """
    Readiness probe - checks all dependencies.
    Returns 200 if all dependencies are healthy, 503 otherwise.
    Used by AWS App Runner to determine if the service can accept traffic.

    Checks:
    - Database: PostgreSQL connection (SELECT 1)
    - Redis: Connection ping
    - Auth Service: /ready endpoint (DB + Redis of auth service)
    - S3: Bucket accessibility (optional, won't fail if unconfigured)
    """
    from services.auth_client import auth_service_client
    from services.storage import check_s3_health

    errors = []
    warnings = []
    details = {}

    # 1. Check Database
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        details["database"] = "ok"
    except Exception as e:
        errors.append(f"Database: {str(e)}")
        details["database"] = str(e)

    # 2. Check Redis
    try:
        redis = await init_redis()
        await redis.ping()
        details["redis"] = "ok"
    except Exception as e:
        errors.append(f"Redis: {str(e)}")
        details["redis"] = str(e)

    # 3. Check Auth Service
    auth_status = await auth_service_client.readiness_check()
    if auth_status["healthy"]:
        details["auth_service"] = "ok"
    else:
        errors.append(f"Auth Service: {auth_status['error']}")
        details["auth_service"] = auth_status

    # 4. Check S3 (warning only if unconfigured, error only if misconfigured/unreachable)
    s3_status = await check_s3_health()
    if s3_status["healthy"]:
        details["s3"] = "ok"
    elif s3_status["status"] == "unconfigured":
        # S3 not configured is a warning, not an error
        warnings.append(f"S3: {s3_status['error']}")
        details["s3"] = s3_status
    else:
        # S3 configured but unhealthy is an error
        errors.append(f"S3: {s3_status['error']}")
        details["s3"] = s3_status

    if errors:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "errors": errors,
                "warnings": warnings,
                "details": details,
            },
        )

    response = {
        "status": "ready",
        "details": details,
    }
    if warnings:
        response["warnings"] = warnings

    return response


# Include routers
app.include_router(author.router)
app.include_router(jury.router)
app.include_router(book.router)  # Books & Reviews
app.include_router(collection.router)  # Collections
app.include_router(upload.router)  # Media Uploads
app.include_router(history.router)  # Edit History
