"""
Cleanup tasks for Library Service.
Handles auto-purging of soft-deleted content and expired uploads.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from celery_app import app
from database import create_worker_session
from models import Author, Book, Collection


@app.task(name="tasks.cleanup.cleanup_soft_deleted_content")
def cleanup_soft_deleted_content() -> dict:
    """
    Hard delete soft-deleted content after 24 hours.

    This task runs daily at 2 AM (configured in celery_app.py beat_schedule).
    Deletes authors, books, and collections that have been soft-deleted
    for more than 24 hours, providing a recovery window for curators.

    Returns:
        Dict with counts of deleted entities
    """

    async def _cleanup():
        WorkerSession, engine = create_worker_session()

        try:
            async with WorkerSession() as db:
                cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)

                # Delete authors
                author_query = delete(Author).where(
                    and_(
                        Author.is_deleted == True,
                        Author.deleted_at <= cutoff_time,
                    )
                )
                author_result = await db.execute(author_query)
                authors_deleted = author_result.rowcount

                # Delete books
                book_query = delete(Book).where(
                    and_(
                        Book.is_deleted == True,
                        Book.deleted_at <= cutoff_time,
                    )
                )
                book_result = await db.execute(book_query)
                books_deleted = book_result.rowcount

                # Delete collections
                collection_query = delete(Collection).where(
                    and_(
                        Collection.is_deleted == True,
                        Collection.deleted_at <= cutoff_time,
                    )
                )
                collection_result = await db.execute(collection_query)
                collections_deleted = collection_result.rowcount

                await db.commit()

                return {
                    "status": "completed",
                    "cutoff_time": cutoff_time.isoformat(),
                    "deleted": {
                        "authors": authors_deleted,
                        "books": books_deleted,
                        "collections": collections_deleted,
                    },
                    "total": authors_deleted + books_deleted + collections_deleted,
                }
        finally:
            await engine.dispose()

    return asyncio.run(_cleanup())
