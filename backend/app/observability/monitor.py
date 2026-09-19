import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.core.safety import redact
from app.db.base import now, seconds
from app.db.models import DetectionReceipt, Incident, MetricSample, TaskExecution, TaskLog, Worker
from app.db.session import session_scope
from app.incidents.detector import detect_task, record_signal
from app.observability.events import emit, task_log
from app.tools import probes
from app.workers.celery_app import celery_app


def receive_event(event):
    kind = event.get("type", "")
    hostname = event.get("hostname")
    if not hostname or kind not in {"worker-online", "worker-heartbeat", "worker-offline"}:
        return
    # Event delivery time is not the heartbeat time: buffered events can arrive
    # after a crash or restart. Never turn an old event into a fresh heartbeat.
    try:
        sent = float(event["timestamp"])
        if not math.isfinite(sent):
            return
        observed_at = datetime.fromtimestamp(sent, UTC)
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return
    received_at = now()
    if observed_at > received_at + timedelta(seconds=5):
        return
    with session_scope() as db:
        worker = db.scalar(select(Worker).where(Worker.name == hostname).with_for_update())
        if not worker:
            worker = Worker(name=hostname)
            db.add(worker)
        elif worker.last_heartbeat.replace(tzinfo=UTC) >= observed_at:
            return
        worker.last_heartbeat = observed_at
        worker.status = (
            "OFFLINE"
            if kind == "worker-offline"
            or seconds(received_at, observed_at) > settings().worker_offline_seconds
            else "ONLINE"
        )


def event_loop(stop):
    while not stop.is_set():
        try:
            with celery_app.connection_for_read() as connection:
                receiver = celery_app.events.Receiver(connection, handlers={"*": receive_event})
                receiver.capture(limit=None, timeout=5, wakeup=True)
        except Exception:
            stop.wait(2)


def sample_workers(db):
    inspector = celery_app.control.inspect(timeout=1)
    observed = {
        name: fn() or {}
        for name, fn in [
            ("active", inspector.active),
            ("reserved", inspector.reserved),
            ("scheduled", inspector.scheduled),
            ("stats", inspector.stats),
            ("queues", inspector.active_queues),
        ]
    }
    names = set().union(*(values.keys() for values in observed.values()))
    for name in names:
        worker = db.scalar(select(Worker).where(Worker.name == name))
        if not worker:
            worker = Worker(name=name)
            db.add(worker)
        worker.last_heartbeat = now()
        worker.status = "ONLINE"
        for key in ["active", "reserved", "scheduled"]:
            items = observed[key].get(name, [])
            setattr(
                worker,
                key,
                [
                    {"id": item.get("request", item).get("id"), "name": item.get("request", item).get("name")}
                    for item in items
                ],
            )
        worker.queues = [q["name"] for q in observed["queues"].get(name, [])]
        raw = observed["stats"].get(name, {})
        worker.completed = sum(raw.get("total", {}).values())
        worker.stats = redact({k: raw.get(k) for k in ["pool", "rusage", "uptime"]})
    for worker in db.scalars(select(Worker)):
        if seconds(now(), worker.last_heartbeat) > settings().worker_offline_seconds:
            was_offline = worker.status == "OFFLINE"
            worker.status = "OFFLINE"
            task = db.scalar(
                select(TaskExecution)
                .where(TaskExecution.worker == worker.name)
                .order_by(TaskExecution.created_at.desc())
                .limit(1)
            )
            if not was_offline or not db.scalar(
                select(Incident.id).where(
                    Incident.worker == worker.name, Incident.status.not_in(["CLOSED", "RECOVERED"])
                )
            ):
                record_signal(
                    db,
                    "worker",
                    "heartbeat_lost",
                    {
                        "worker": worker.name,
                        "status": "OFFLINE",
                        "heartbeat_age_seconds": seconds(now(), worker.last_heartbeat),
                    },
                    task,
                    worker=worker.name,
                )
            for active in db.scalars(
                select(TaskExecution).where(
                    TaskExecution.worker == worker.name, TaskExecution.status == "STARTED"
                )
            ):
                if seconds(now(), active.started_at) > 35:
                    active.status = "LOST" if active.idempotent else "UNCERTAIN"
                    active.exception = "WorkerLostError: worker heartbeat absent during task execution"
                    task_log(db, active.id, "ERROR", active.exception)


def sample(db):
    sample_workers(db)
    errors = list(
        db.scalars(
            select(TaskLog)
            .outerjoin(DetectionReceipt, DetectionReceipt.log_id == TaskLog.id)
            .where(TaskLog.level == "ERROR", DetectionReceipt.log_id.is_(None))
            .order_by(TaskLog.created_at)
            .limit(100)
        )
    )
    for log in errors:
        task = db.get(TaskExecution, log.task_id)
        if task.status in {"FAILED", "RETRYING", "QUARANTINED", "UNCERTAIN", "LOST"}:
            detect_task(db, task)
        else:
            record_signal(
                db,
                "task",
                "transient_error",
                {"exception": log.message, "retry_count": log.data.get("retry_count", 0)},
                task,
            )
        db.add(DetectionReceipt(log_id=log.id))
    queue = probes.queue_metrics()
    broker = probes.redis_status(settings().broker_probe_url)
    previous = db.scalar(select(MetricSample).order_by(MetricSample.created_at.desc()).limit(1))
    task = db.scalar(
        select(TaskExecution)
        .where(TaskExecution.queue == "experiment")
        .order_by(TaskExecution.created_at.desc())
        .limit(1)
    )
    if broker["status"] == "UNAVAILABLE" and (
        not previous or previous.values.get("broker_status") != "UNAVAILABLE"
    ):
        record_signal(db, "broker", "connectivity_lost", {"broker": broker}, task)
    for name, depth in queue["depths"].items():
        if (
            depth > settings().backlog_threshold
            and previous
            and previous.values.get("queue_depths", {}).get(name, 0) > settings().backlog_threshold
        ):
            related = db.scalar(
                select(TaskExecution)
                .where(
                    TaskExecution.queue == name, TaskExecution.status.in_(["QUEUED", "STARTED", "PENDING"])
                )
                .order_by(TaskExecution.created_at.desc())
                .limit(1)
            )
            record_signal(
                db,
                "queue",
                "persistent_backlog",
                {"queue": name, "depth": depth, "threshold": settings().backlog_threshold},
                related,
            )
    recent = list(
        db.scalars(select(TaskExecution).where(TaskExecution.completed_at >= now() - timedelta(seconds=60)))
    )
    failure_count = sum(t.status in {"FAILED", "UNCERTAIN", "QUARANTINED"} for t in recent)
    durations = [seconds(t.completed_at, t.started_at) for t in recent if t.started_at]
    if len(recent) >= settings().alert_min_completions:
        rate = failure_count / len(recent)
        if rate >= settings().failure_rate_threshold:
            failed = next(
                (t for t in recent if t.status in {"FAILED", "UNCERTAIN", "QUARANTINED"}), recent[0]
            )
            record_signal(
                db,
                "task",
                "high_failure_rate",
                {"failure_rate": rate, "completed_tasks": len(recent), "window_seconds": 60},
                failed,
            )
        if durations and sum(durations) / len(durations) > settings().latency_threshold_seconds:
            slow = max(
                (t for t in recent if t.started_at), key=lambda t: seconds(t.completed_at, t.started_at)
            )
            record_signal(
                db,
                "task",
                "high_latency",
                {
                    "mean_latency_seconds": sum(durations) / len(durations),
                    "threshold_seconds": settings().latency_threshold_seconds,
                },
                slow,
            )
    workers = list(db.scalars(select(Worker)))
    values = {
        "tasks_per_minute": len(recent),
        "failure_rate": failure_count / len(recent) if recent else None,
        "mean_latency_seconds": sum(durations) / len(durations) if durations else None,
        "queue_depths": queue["depths"],
        "active_workers": sum(w.status == "ONLINE" for w in workers),
        "worker_count": len(workers),
        "broker_status": broker["status"],
        "active_incidents": db.scalar(
            select(func.count(Incident.id)).where(Incident.status.not_in(["CLOSED", "RECOVERED"]))
        )
        or 0,
    }
    db.add(MetricSample(values=values))
    emit(db, "metrics", "system", values)