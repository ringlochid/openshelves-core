from celery import Celery
from celery.schedules import crontab

from settings import settings

app = Celery(
    "library_app",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

app.conf.update(
    task_default_queue="default",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "tasks.media.*": {"queue": "media"},
        "tasks.email.*": {"queue": "email"},
        "tasks.analytics.*": {"queue": "analytics"},
        "tasks.cleanup.*": {"queue": "default"},
    },
    # Beat schedule (Celery Beat for periodic tasks)
    beat_schedule={
        # Cleanup soft-deleted content daily at 2 AM
        "cleanup-soft-deleted-daily": {
            "task": "tasks.cleanup.cleanup_soft_deleted_content",
            "schedule": crontab(hour="2", minute="0"),
        },
        # Cleanup expired uploads hourly
        "cleanup-expired-uploads-hourly": {
            "task": "tasks.cleanup.cleanup_expired_uploads",
            "schedule": crontab(minute="0"),
        },
        "sync-view-counts-hourly": {
            "task": "tasks.analytics.sync_view_counts",
            "schedule": crontab(minute="15"),  # every hour 15 min
        },
        "sync-average-ratings-hourly": {
            "task": "tasks.analytics.recalculate_average_ratings",
            "schedule": crontab(minute="10"),
        },
        # Trending scores: Every 6 hours (CPU-intensive calculation)
        "calculate-trending-scores": {
            "task": "tasks.analytics.calculate_trending_scores",
            "schedule": crontab(hour="*/6", minute="30"),  # 4x/day
        },
    },
)

# Ensure tasks under app.tasks.* are registered
app.autodiscover_tasks(["tasks"])
