import os
from celery import Celery

broker_url = (
    os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL", "redis://localhost:6379/0")
)

backend_url = (
    os.getenv("CELERY_RESULT_BACKEND")
    or "redis://localhost:6379/1"    # safer than same DB as broker
)

app = Celery("library_app", broker=broker_url, backend=backend_url)

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
    },
)

@app.task
def ping():
    return "pong"