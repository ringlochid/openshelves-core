from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cache import close_redis, init_redis
from dependencies.auth import load_jwt_public_key
#from routers import author, book, review
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
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
from routers import author, jury, book

app.include_router(author.router)
app.include_router(jury.router)
app.include_router(book.router)  # Phase 3: Books & Reviews

# Legacy routers disabled (awaiting Phase 2-4 rewrite)
# from routers import review (review endpoints now in book router)
# app.include_router(review.router)
