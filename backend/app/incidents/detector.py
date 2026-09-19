import hashlib
from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.safety import redact
from app.db.base import now, uid
from app.db.models import Evidence, Incident, RuntimeSetting
from app.observability.events import incident_event


def record_signal(db, component, kind, symptoms, task=None, scope="local", worker=None):
    scope = task.scope_id if task else scope
    queue = task.queue if task else ("experiment" if scope != "local" else None)
    fingerprint = hashlib.sha256(f"{scope}:{component}:{kind}:{queue}:{worker or ''}".encode()).hexdigest()
    cutoff = now() - timedelta(seconds=settings().detection_window_seconds)
    incident = db.scalar(
        select(Incident)
        .where(
            Incident.fingerprint == fingerprint,
            Incident.last_seen_at >= cutoff,
            Incident.status.not_in(["RECOVERED", "CLOSED"]),
        )
        .with_for_update()
    )
    clean = redact(symptoms)
    if incident:
        incident.occurrence_count += 1
        incident.last_seen_at = now()
        incident_event(
            db,
            incident.id,
            "Related Event",
            {"kind": kind, "symptoms": clean, "task_id": task.id if task else None},
        )
        return incident
    auto = db.get(RuntimeSetting, "auto:" + scope)
    incident = Incident(
        id=uid(),
        title=f"{component.title()}: {kind.replace('_', ' ')}",
        description=f"Operational signal {kind} in {component}.",
        component=component,
        symptoms=clean,
        fingerprint=fingerprint,
        scope_id=scope,
        task_id=task.id if task else None,
        worker=task.worker if task else worker,
        queue=queue,
        correlation_id=task.correlation_id if task else None,
        trace_id=task.trace_id if task else None,
        investigation_requested=settings().auto_investigate
        and (scope == "local" or bool(auto and auto.value)),
    )
    db.add(incident)
    db.flush()
    db.add(
        Evidence(
            incident_id=incident.id,
            kind="initial_signal",
            source="detector",
            component=component,
            retrieval_method="operational_event",
            content={"signal": kind, **clean},
        )
    )
    incident_event(db, incident.id, "Evidence Collection", {"signal": kind, "symptoms": clean})
    return incident


def detect_task(db, task):
    if task.status not in {"FAILED", "RETRYING", "QUARANTINED", "UNCERTAIN", "LOST"}:
        return None
    error = task.exception or ""
    component = (
        "dependency" if any(t in error for t in ["Timeout", "HTTPStatusError", "ConnectError"]) else "task"
    )
    if "OperationalError" in error:
        component = "database"
    if "WorkerLostError" in error:
        component = "worker"
    return record_signal(
        db,
        component,
        "execution_failure",
        {"status": task.status, "exception": error, "retry_count": task.retry_count},
        task,
    )
