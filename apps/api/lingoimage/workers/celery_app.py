"""Celery application and beat schedule."""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging, task_postrun, task_prerun, worker_ready

from lingoimage.core.config import settings
from lingoimage.core.logging import bind_request, clear_context, configure_logging, get_logger

log = get_logger(__name__)

celery_app = Celery("lingoimage", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=settings.job_soft_time_limit_seconds,
    task_time_limit=settings.job_hard_time_limit_seconds,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    worker_send_task_events=True,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": settings.job_hard_time_limit_seconds + 120},
    task_default_queue=settings.queue_default,
    task_queues_ha_policy="all",
    task_routes={
        "lingoimage.execute_job": {"queue": settings.queue_default},
        "lingoimage.build_export": {"queue": "export"},
        "lingoimage.deliver_webhook": {"queue": "notifications"},
        "lingoimage.send_email": {"queue": "notifications"},
        "lingoimage.*_maintenance": {"queue": "notifications"},
    },
    beat_schedule={
        "lifecycle-sweep": {
            "task": "lingoimage.lifecycle_maintenance",
            "schedule": 900.0,  # every 15 minutes
        },
        "stuck-jobs": {
            "task": "lingoimage.stuck_jobs_maintenance",
            "schedule": 300.0,
        },
        "webhook-retries": {
            "task": "lingoimage.webhook_retry_maintenance",
            "schedule": 60.0,
        },
        "monthly-credits": {
            "task": "lingoimage.monthly_credits_maintenance",
            "schedule": 3600.0,
        },
        "provider-health": {
            "task": "lingoimage.provider_health_maintenance",
            "schedule": 300.0,
        },
    },
)

celery_app.autodiscover_tasks(["lingoimage.workers"], related_name="tasks", force=True)


@setup_logging.connect
def _configure_logging(**_kwargs: object) -> None:
    configure_logging(force=True)


@worker_ready.connect
def _on_ready(**_kwargs: object) -> None:
    log.info("worker.ready", queues=settings.queue_default)


@task_prerun.connect
def _on_prerun(task_id: str | None = None, args: tuple | None = None, **_kwargs: object) -> None:
    job_id = args[0] if args else None
    bind_request(request_id=f"task_{task_id}", job_id=str(job_id) if job_id else None)


@task_postrun.connect
def _on_postrun(**_kwargs: object) -> None:
    clear_context()


# Importing registers the tasks with this app instance.
from lingoimage.workers import tasks  # noqa: E402,F401
