"""Analytics/statistics tasks."""

import asyncio
import math
from datetime import datetime, timezone
from sqlalchemy import select, func

from celery_app import app
from database import create_worker_session
from models import Book, Review
import cache


@app.task(name="tasks.analytics.sync_view_counts")
def sync_view_counts() -> dict:
    """
    Sync view counts from Redis HyperLogLog to database.

    This task runs hourly (configured in celery_app.py beat_schedule).
    Reads unique view counts from Redis and updates Book.view_count.

    Returns:
        Dict with count of updated books
    """

    async def _sync():
        # Create fresh connections for this worker task
        WorkerSession, engine = create_worker_session()

        try:
            async with WorkerSession() as db:
                # Get all public books
                result = await db.execute(
                    select(Book).where(
                        Book.is_public == True,
                        Book.is_deleted == False,
                    )
                )
                books = result.scalars().all()

                # Initialize Redis
                r = await cache.init_redis()
                updated_count = 0

                for book in books:
                    # Read view count from Redis HyperLogLog
                    redis_key = f"views:book:{book.id}"
                    view_count = await r.pfcount(redis_key)

                    # Update if changed
                    if view_count != book.view_count:
                        book.view_count = view_count
                        updated_count += 1

                await db.commit()

                return {
                    "status": "completed",
                    "books_checked": len(books),
                    "books_updated": updated_count,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        finally:
            await engine.dispose()

    return asyncio.run(_sync())


@app.task(name="tasks.analytics.calculate_trending_scores")
def calculate_trending_scores() -> dict:
    """
    Calculate trending scores for all public books using Reddit-style algorithm.

    This task runs every 6 hours (configured in celery_app.py beat_schedule).

    Formula: log10(max(views, 1)) / ((age_hours + 2) ^ gravity)
    - Recent books with moderate views rank higher than old books with high views
    - Logarithmic scaling prevents mega-hits from dominating
    - Gravity (1.8) controls time decay rate

    Returns:
        Dict with count of updated books
    """

    async def _calculate():
        # Create fresh connections for this worker task
        WorkerSession, engine = create_worker_session()

        try:
            async with WorkerSession() as db:
                # Get all public books
                result = await db.execute(
                    select(Book).where(
                        Book.is_public == True,
                        Book.is_deleted == False,
                    )
                )
                books = result.scalars().all()

                now = datetime.now(timezone.utc)
                updated_count = 0

                # Constants
                GRAVITY = 1.5  # Time decay factor (1.5-2.0 recommended)

                for book in books:
                    # Popularity signal (logarithmic scaling)
                    views = max(book.view_count, 1)
                    popularity = math.log10(views)

                    # Time decay
                    age_seconds = (now - book.updated_at).total_seconds()
                    age_hours = age_seconds / 3600
                    time_decay = (age_hours + 2) ** GRAVITY

                    # Calculate and store trending score
                    new_score = popularity / time_decay

                    if (
                        abs(new_score - book.trending_score) > 0.001
                    ):  # Update if changed
                        book.trending_score = new_score
                        updated_count += 1

                await db.commit()

                return {
                    "status": "completed",
                    "books_checked": len(books),
                    "books_updated": updated_count,
                    "timestamp": now.isoformat(),
                }
        finally:
            await engine.dispose()

    return asyncio.run(_calculate())


@app.task(name="tasks.analytics.recalculate_average_ratings")
def recalculate_average_ratings() -> dict:
    """
    Recalculate average ratings for all books from reviews.

    This task runs daily (can be added to beat_schedule if needed).
    Aggregates ratings from Review table and updates Book.average_rating.

    Returns:
        Dict with count of updated books
    """

    async def _recalculate():
        # Create fresh connections for this worker task
        WorkerSession, engine = create_worker_session()

        try:
            async with WorkerSession() as db:
                # Get all public books with reviews
                result = await db.execute(
                    select(Book.id, func.avg(Review.rating).label("avg_rating"))
                    .join(Review, Book.id == Review.book_id)
                    .where(
                        Book.is_public == True,
                        Book.is_deleted == False,
                    )
                    .group_by(Book.id)
                )

                rating_data = result.all()
                updated_count = 0

                for book_id, avg_rating in rating_data:
                    book = await db.get(Book, book_id)
                    if book and avg_rating is not None:
                        new_rating = round(float(avg_rating), 2)
                        if abs(new_rating - book.average_rating) > 0.01:
                            book.average_rating = new_rating
                            updated_count += 1

                # Set 0.0 for books with no reviews
                books_without_reviews = await db.execute(
                    select(Book)
                    .outerjoin(Review, Book.id == Review.book_id)
                    .where(
                        Book.is_public == True,
                        Book.is_deleted == False,
                        Review.id.is_(None),
                        Book.average_rating != 0.0,
                    )
                )

                for book in books_without_reviews.scalars():
                    book.average_rating = 0.0
                    updated_count += 1

                await db.commit()

                return {
                    "status": "completed",
                    "books_updated": updated_count,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        finally:
            await engine.dispose()

    return asyncio.run(_recalculate())
