from datetime import timedelta

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.base import now, seconds
from app.db.models import (
    ApprovalRequest,
    Diagnosis,
    Incident,
    Investigation,
    Remediation,
    RemediationExecution,
    RuntimeSetting,
    TaskExecution,
    Verification,
)
from app.observability.events import incident_event
from app.remediation.policy import PolicyContext, evaluate


def latest_diagnosis(db, incident_id):
    return db.scalar(
        select(Diagnosis)
        .join(Investigation)
        .where(Investigation.incident_id == incident_id)
        .order_by(Diagnosis.created_at.desc())
        .limit(1)
    )


def context_for(db, incident, diagnosis, exclude=None):
    cfg = settings()
    task = db.get(TaskExecution, incident.task_id) if incident.task_id else None
    verification = db.scalar(
        select(Verification)
        .where(Verification.diagnosis_id == diagnosis.id)
        .order_by(Verification.created_at.desc())
        .limit(1)
    )
    previous = list(
        db.scalars(
            select(Remediation).where(
                Remediation.incident_id == incident.id, Remediation.id != (exclude or "")
            )
        )
    )
    executions = list(
        db.scalars(
            select(RemediationExecution).where(
                RemediationExecution.remediation_id.in_([r.id for r in previous])
            )
        )
    )
    context = PolicyContext(
        bool(verification and verification.verified),
        diagnosis.confidence,
        cfg.remediation_min_confidence,
        bool(task and task.idempotent),
        task.status if task else "",
        bool(task and task.quarantined),
        incident.worker,
        incident.queue,
        any(e.status in {"FAILED", "UNCERTAIN"} for e in executions),
        any(
            r.action == diagnosis.recommended_action
            and seconds(now(), r.created_at) < cfg.remediation_cooldown_seconds
            for r in previous
        ),
        cfg.app_environment,
    )
    return context, verification


def propose(db, incident, diagnosis):
    old = db.scalar(select(Remediation).where(Remediation.diagnosis_id == diagnosis.id))
    if old:
        return old
    context, verification = context_for(db, incident, diagnosis)
    policy = evaluate(diagnosis.recommended_action, context)
    mode = settings().remediation_mode
    remediation = Remediation(
        incident_id=incident.id,
        diagnosis_id=diagnosis.id,
        verification_id=verification.id if verification else None,
        action=diagnosis.recommended_action,
        parameters={"task_id": incident.task_id, "worker": incident.worker, "queue": incident.queue},
        risk=policy.risk,
        policy_decision=policy.decision,
        reason=policy.reason,
        mode=mode,
        status="DRY_RUN" if mode == "dry_run" else policy.decision,
        requested=mode == "execute" and policy.decision == "ALLOW",
    )
    db.add(remediation)
    db.flush()
    if mode == "execute" and policy.decision == "APPROVAL_REQUIRED":
        db.add(ApprovalRequest(remediation_id=remediation.id, expires_at=now() + timedelta(minutes=15)))
        incident.status = "APPROVAL_REQUIRED"
    incident_event(
        db,
        incident.id,
        "Remediation",
        {
            "id": remediation.id,
            "action": remediation.action,
            "mode": mode,
            "decision": policy.decision,
            "reason": policy.reason,
        },
    )
    return remediation


def decide_approval(db, remediation_id, approved, actor):
    approval = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.remediation_id == remediation_id).with_for_update()
    )
    if not approval or approval.status != "PENDING":
        raise ValueError("Approval is missing or already decided")
    if seconds(now(), approval.expires_at) > 0:
        raise ValueError("Approval expired; investigate again")
    approval.status = "APPROVED" if approved else "REJECTED"
    approval.decided_by = actor
    approval.decided_at = now()
    remediation = db.get(Remediation, remediation_id)
    remediation.requested = approved
    remediation.status = approval.status
    incident_event(
        db,
        remediation.incident_id,
        "Approval",
        {"approved": approved, "actor": actor, "remediation_id": remediation_id},
    )
    return approval


def dispatch_action(db, remediation):
    from app.workers.celery_app import celery_app

    action, params = remediation.action, remediation.parameters
    task = db.get(TaskExecution, params["task_id"]) if params.get("task_id") else None
    if action == "RETRY_TASK":
        task.status = "PENDING"
        task.dispatch_after = now() + timedelta(seconds=4)
        return {"scheduled_task_id": task.id, "retry_history_preserved": True}
    if action == "QUARANTINE_TASK":
        task.quarantined = True
        task.status = "QUARANTINED"
        return {"quarantined_task_id": task.id}
    if action == "CLEAR_RETRY_STATE":
        result = {
            "previous_retry_count": task.retry_count,
            "previous_delivery_count": task.delivery_count,
            "enqueued": False,
        }
        task.retry_count = 0
        task.delivery_count = 0
        task.quarantined = False
        task.status = "FAILED"
        return result
    if action == "RESTART_WORKER":
        cfg = settings()
        with httpx.Client(timeout=8) as client:
            response = client.post(
                cfg.worker_control_url + "/restart", headers={"X-Control-Token": cfg.control_token}
            )
            response.raise_for_status()
        return {"worker": "experiment@autopilot", "restart_requested": True}
    if action in {"PAUSE_QUEUE", "RESUME_QUEUE"}:
        queue = params["queue"]
        destination = ["experiment@autopilot" if queue == "experiment" else "default@autopilot"]
        operation = (
            celery_app.control.cancel_consumer if action == "PAUSE_QUEUE" else celery_app.control.add_consumer
        )
        replies = operation(queue, destination=destination, reply=True, timeout=3)
        if not replies or any("error" in value for reply in replies for value in reply.values()):
            raise RuntimeError("Worker did not acknowledge consumer change")
        record = db.get(RuntimeSetting, "paused:" + queue)
        if not record:
            record = RuntimeSetting(key="paused:" + queue, value=False)
            db.add(record)
        record.value = action == "PAUSE_QUEUE"
        record.updated_at = now()
        return {"queue": queue, "paused": record.value}
    raise ValueError("No dispatcher for this action")


def execute_remediation(db, remediation):
    remediation = db.scalar(select(Remediation).where(Remediation.id == remediation.id).with_for_update())
    if not remediation.requested or db.scalar(
        select(RemediationExecution).where(RemediationExecution.remediation_id == remediation.id)
    ):
        return None
    remediation.requested = False
    incident = db.get(Incident, remediation.incident_id)
    diagnosis = latest_diagnosis(db, incident.id)
    if (
        settings().remediation_mode != "execute"
        or remediation.mode != "execute"
        or not diagnosis
        or diagnosis.id != remediation.diagnosis_id
    ):
        remediation.status = "DENIED"
        remediation.reason = "Mode changed or diagnosis superseded"
        return None
    context, _ = context_for(db, incident, diagnosis, remediation.id)
    policy = evaluate(remediation.action, context)
    approval = db.scalar(select(ApprovalRequest).where(ApprovalRequest.remediation_id == remediation.id))
    approved = bool(approval and approval.status == "APPROVED" and seconds(now(), approval.expires_at) == 0)
    if policy.decision != "ALLOW" and not (policy.decision == "APPROVAL_REQUIRED" and approved):
        remediation.status = "DENIED"
        remediation.reason = policy.reason
        return None
    execution = RemediationExecution(
        remediation_id=remediation.id, status="EXECUTING", automatic=not approved
    )
    db.add(execution)
    remediation.status = "EXECUTING"
    incident.status = "REMEDIATING"
    db.commit()
    try:
        execution.result = dispatch_action(db, remediation)
        execution.status = "OBSERVING"
        execution.observe_after = now() + timedelta(seconds=settings().observation_seconds)
        remediation.status = "OBSERVING"
    except Exception as exc:
        execution.status = "UNCERTAIN"
        execution.result = {
            "error_type": type(exc).__name__,
            "message": "Action outcome requires manual review; it will not be replayed",
        }
        execution.completed_at = now()
        remediation.status = "UNCERTAIN"
        incident.status = "HUMAN_REVIEW"
    incident_event(
        db,
        incident.id,
        "Execution",
        {"execution_id": execution.id, "status": execution.status, "result": execution.result},
    )
    db.commit()
    return execution
