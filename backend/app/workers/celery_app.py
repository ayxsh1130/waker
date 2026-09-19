from celery import Celery

from app.core.config import settings

cfg = settings()
celery_app = Celery(
    "autopilot",
    broker=cfg.celery_broker_url or cfg.redis_url,
    backend=cfg.redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_default_queue="default",
    task_track_started=True,
    worker_send_task_events=True,
    task_send_sent_event=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_soft_time_limit=25,
    task_time_limit=30,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={"visibility_timeout": 70, "socket_connect_timeout": 3, "socket_timeout": 3},
    result_backend_transport_options={"visibility_timeout": 70},
    visibility_timeout=70,
    task_publish_retry=False,
    timezone="UTC",
    enable_utc=True,
)
