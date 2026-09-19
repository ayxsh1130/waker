import hashlib
import json
from datetime import timedelta

from opentelemetry import propagate, trace
from sqlalchemy import select

from app.db.base import now, uid
from app.db.models import RuntimeSetting, TaskExecution
from app.observability.events import task_log


def create_task(db, request, scope="local", queue="default"):
    payload = request.model_dump(exclude={"name", "idempotency_key"})
    digest = hashlib.sha256(
        json.dumps({"name": request.name, **payload}, sort_keys=True).encode()
    ).hexdigest()
    old = db.scalar(select(TaskExecution).where(TaskExecution.idempotency_key == request.idempotency_key))
    if old:
        if old.payload_hash != digest:
            raise ValueError("Idempotency key already used with different payload")
        return old
    carrier = {}
    propagate.inject(carrier)
    tid = trace.get_current_span().get_span_context().trace_id
    task = TaskExecution(
        id=uid(),
        name=request.name,
        queue=queue,
        payload=payload,
        payload_hash=digest,
        idempotency_key=request.idempotency_key,
        idempotent=request.name != "send_email",
        correlation_id=uid(),
        trace_id=f"{tid:032x}" if tid else uid().replace("-", ""),
        trace_context=carrier,
        scope_id=scope,
    )
    db.add(task)
    db.flush()
    task_log(db, task.id, "INFO", "Task accepted for durable dispatch", {"name": task.name, "queue": queue})
    return task


def dispatch_pending(db):
    from app.workers.celery_app import celery_app

    tasks = db.scalars(
        select(TaskExecution)
        .where(TaskExecution.status == "PENDING", TaskExecution.dispatch_after <= now())
        .with_for_update(skip_locked=True)
        .limit(30)
    ).all()
    for task in tasks:
        paused = db.get(RuntimeSetting, "paused:" + task.queue)
        if paused and paused.value is True:
            continue
        try:
            celery_app.send_task(
                "autopilot.execute",
                args=[task.id],
                task_id=task.id,
                queue=task.queue,
                headers={**task.trace_context, "correlation_id": task.correlation_id},
            )
            task.published_at = now()
            task.status = "QUEUED"
        except Exception as exc:
            task.dispatch_attempts += 1
            task.dispatch_after = now() + timedelta(seconds=min(30, 2 ** min(task.dispatch_attempts, 5)))
            task_log(db, task.id, "ERROR", "Broker dispatch failed", {"error_type": type(exc).__name__})
            from app.incidents.detector import record_signal

            record_signal(db, "broker", "dispatch_failure", {"error_type": type(exc).__name__}, task)
