"""
# Celery application configuration
# Broker: Redis DB 2
# Queues: default, calls (high priority), analytics (low priority)
# Routing: post_call → calls queue, intelligence → analytics queue
# Beat schedule: check_campaigns every 30 seconds
"""
# File: voice-agent-platform/app/tasks/__init__.py
"""
Celery application instance and configuration.
Run workers with: celery -A app.tasks.celery_app worker -l info -c 4
Run beat with:    celery -A app.tasks.celery_app beat -l info
"""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "voice_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "calls": {"exchange": "calls", "routing_key": "calls"},
        "analytics": {"exchange": "analytics", "routing_key": "analytics"},
    },
    task_routes={
        "tasks.process_post_call": {"queue": "calls"},
        "tasks.extract_conversation_intelligence": {"queue": "analytics"},
        "tasks.schedule_outbound_call": {"queue": "calls"},
    },
    beat_schedule={
        # Campaign scheduler — runs every 30 seconds to check for pending outbound calls
        "check-pending-campaigns": {
            "task": "tasks.check_campaigns",
            "schedule": 30.0,
        },
    },
)

# Auto-discover tasks in all task modules
celery_app.autodiscover_tasks(["app.tasks"])