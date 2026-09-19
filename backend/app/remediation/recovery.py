from datetime import timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.db.base import now, seconds
from app.db.models import Incident, Remediation, RemediationExecution, TaskExecution, TaskLog, Worker
from app.observability.events import incident_event
from app.retrieval.client import remember_recovered
from app.tools import probes


def classify_recovery(action, task_status, workers_healthy, dependency_healthy, queue_depth, new_failures):
    if action in {"QUARANTINE_TASK", "PAUSE_QUEUE", "CLEAR_RETRY_STATE"}:
        return "PARTIALLY_RECOVERED"
    if (
        task_status == "SUCCEEDED"
        and workers_healthy
        and dependency_healthy
        and queue_depth is not None
        and queue_depth <= settings().backlog_threshold
        and new_failures == 0
    ):
        return "RECOVERED"
    if task_status in {"PENDING", "QUEUED", "STARTED", "RETRYING"} and new_failures == 0:
        return "PARTIALLY_RECOVERED"
    return "FAILED"


def observe_recoveries(db):
    for execution in db.scalars(
        select(RemediationExecution).where(
            RemediationExecution.status == "OBSERVING", RemediationExecution.observe_after <= now()
        )
    ):
        remediation = db.get(Remediation, execution.remediation_id)
        incident = db.get(Incident, remediation.incident_id)
        task = db.get(TaskExecution, incident.task_id) if incident.task_id else None
        worker = db.scalar(select(Worker).where(Worker.name == (task.worker if task else incident.worker)))
        queues = probes.queue_metrics()
        broker = probes.redis_status(settings().broker_probe_url if incident.queue == "experiment" else None)
        dependency = (
            probes.external_status(incident.scope_id)
            if incident.component == "dependency"
            else {"status": "HEALTHY", "checked": False}
        )
        failures = (
            db.scalar(
                select(func.count(TaskLog.id))
                .join(TaskExecution, TaskExecution.id == TaskLog.task_id)
                .where(
                    TaskExecution.scope_id == incident.scope_id,
                    TaskLog.level == "ERROR",
                    TaskLog.created_at > execution.created_at,
                )
            )
            or 0
        )
        outcome = classify_recovery(
            remediation.action,
            task.status if task else None,
            bool(worker and worker.status == "ONLINE" and broker["status"] == "HEALTHY"),
            dependency["status"] == "HEALTHY",
            queues["depths"].get(incident.queue),
            failures,
        )
        # A task still running at the first observation is not a final outcome.
        # Recheck within a bounded window; never dispatch the action again.
        pending = (
            remediation.action not in {"QUARANTINE_TASK", "PAUSE_QUEUE", "CLEAR_RETRY_STATE"}
            and task is not None
            and task.status in {"PENDING", "QUEUED", "STARTED", "RETRYING"}
            and failures == 0
        )
        if pending and seconds(now(), execution.created_at) < settings().observation_seconds * 3:
            execution.observe_after = now() + timedelta(seconds=5)
            execution.post_result = {"task_status": task.status, "detail": "Waiting for task completion"}
            continue
        execution.status = outcome
        execution.completed_at = now()
        execution.post_result = {
            "task_status": task.status if task else None,
            "worker_status": worker.status if worker else "UNKNOWN",
            "queue": queues,
            "broker": broker,
            "dependency": dependency,
            "new_failure_logs": failures,
        }
        remediation.status = outcome
        incident.status = outcome
        if outcome == "RECOVERED":
            incident.recovered_at = now()
        incident_event(db, incident.id, "Recovery", {"outcome": outcome, **execution.post_result})
        db.flush()
        if outcome == "RECOVERED":
            from app.db.models import Diagnosis

            remember_recovered(db, incident, db.get(Diagnosis, remediation.diagnosis_id))