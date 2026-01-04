# app/celery_app.py
"""Celery application configuration for YouTube Digest."""
from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "youtube_digest",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task behavior
    task_track_started=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # Fair task distribution

    # Result expiration (7 days)
    result_expires=604800,

    # Beat schedule (periodic tasks)
    beat_schedule={
        "check-for-new-videos-daily": {
            "task": "app.tasks.check_for_new_videos",
            "schedule": 86400.0,  # 24 hours in seconds
        },
        "generate-digest-biweekly": {
            "task": "app.tasks.generate_and_send_digest",
            "schedule": 1209600.0,  # 14 days in seconds
        },
    },

    # Beat scheduler persistence
    beat_scheduler="celery.beat:PersistentScheduler",
    beat_schedule_filename="celerybeat-schedule",
)
