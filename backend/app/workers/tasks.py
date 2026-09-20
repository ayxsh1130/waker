import traceback

from celery import signals
from opentelemetry import propagate, trace
from sqlalchemy import select

from app.core.safety import redact
from app.db.base import now, seconds
from app.db.models import TaskExecution
from app.db.session import session_scope
from app.observability.events import task_log
from app.workers.celery_app import celery_app
from app.workers.operations import run_operation


@signals.worker_process_init.connect
def instrument_worker(**kwargs):
    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    from app.db.session import engine
    from app.observability.telemetry import configure_telemetry

    engine().dispose(close=False)
    configure_telemetry("autopilot-worker")
    CeleryInstrumentor().instrument()


@celery_app.task(bind=True, name="autopilot.execute", max_retries=12)
def execute(self, task_id):
    with session_scope() as db:
        task = db.scalar(select(TaskExecution).where(TaskExecution.id == task_id).with_for_update())
        if not task:
            return {"status": "UNKNOWN_TASK"}
        if task.status in {"SUCCEEDED", "UNCERTAIN"} or task.quarantined:
            return {"status": "DUPLICATE_OR_QUARANTINED"}
        if task.status == "STARTED":
            if not task.idempotent:
                task.status = "UNCERTAIN"
                task_log(
                    db, task.id, "ERROR", "Non-idempotent execution outcome uncertain; manual review required"
                )
                return {"status": "UNCERTAIN"}
            if task.started_at and seconds(now(), task.started_at) < 35:
                raise self.retry(countdown=10)
        task.delivery_count += 1
        if task.delivery_count > 5:
            task.quarantined = True
            task.status = "QUARANTINED"
            task_log(db, task.id, "ERROR", "Delivery safety limit exceeded")
            return {"status": "QUARANTINED"}
        task.status = "STARTED"
        task.worker = self.request.hostname
        task.started_at = now()
        task_log(
            db,
            task.id,
            "INFO",
            "Execution started",
            {"worker": task.worker, "correlation_id": task.correlation_id},
        )
        name, payload, scope, correlation, carrier = (
            task.name,
            task.payload,
            task.scope_id,
            task.correlation_id,
            task.trace_context,
        )
    try:
        with trace.get_tracer("autopilot.workload").start_as_current_span(
            name, context=propagate.extract(carrier)
        ) as span:
            span.set_attribute("task.id", task_id)
            span.set_attribute("correlation.id", correlation)
            result = run_operation(name, payload, task_id, scope, correlation)
        with session_scope() as db:
            task = db.get(TaskExecution, task_id)
            task.status = "SUCCEEDED"
            task.completed_at = now()
            task.result = result
            task.exception = None
            task.stack_trace = None
            task_log(db, task.id, "INFO", "Execution succeeded", result)
        return result
    except Exception as exc:
        with session_scope() as db:
            task = db.get(TaskExecution, task_id)
            task.exception = redact(f"{type(exc).__name__}: {exc}")
            task.stack_trace = redact(traceback.format_exc())
            task.retry_count += 1
            retry = task.idempotent and task.retry_count <= 3 and not task.quarantined
            task.status = "RETRYING" if retry else "FAILED" if task.idempotent else "UNCERTAIN"
            if not retry:
                task.completed_at = now()
                task.quarantined = isinstance(exc, ValueError) and task.retry_count >= 4
            task_log(
                db,
                task.id,
                "ERROR",
                task.exception,
                {
                    "retry_count": task.retry_count,
                    "stack_trace": task.stack_trace,
                    "correlation_id": correlation,
                },
            )
            countdown = min(20, 2**task.retry_count)
        if retry:
            raise self.retry(exc=exc, countdown=countdown) from exc
        raise
