from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from routers import author, jury, book
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
    Readiness probe - checks DB and Redis connectivity.
    Returns 200 if all dependencies are healthy, 503 otherwise.
    Used by AWS App Runner to determine if the service can accept traffic.
    """
    errors = []

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        errors.append(f"Database: {str(e)}")

    try:
        redis = await init_redis()
        await redis.ping()
    except Exception as e:
        errors.append(f"Redis: {str(e)}")

    if errors:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "errors": errors},
        )

    return {"status": "ready", "database": "ok", "redis": "ok"}


# Include routers
app.include_router(author.router)
app.include_router(jury.router)
app.include_router(book.router)  # Phase 3: Books & Reviews

# Legacy routers disabled (awaiting Phase 2-4 rewrite)
# from routers import review (review endpoints now in book router)
# app.include_router(review.router)
