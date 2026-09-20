from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import (
    ApprovalRequest,
    Diagnosis,
    Evidence,
    Incident,
    IncidentEvent,
    Investigation,
    InvestigationStep,
    LLMUsage,
    MetricSample,
    Remediation,
    RemediationExecution,
    TaskExecution,
    TaskLog,
    Verification,
    Worker,
)
from app.db.session import row_dict
from app.tools import probes


def incident_detail(db, incident):
    investigations = list(
        db.scalars(
            select(Investigation)
            .where(Investigation.incident_id == incident.id)
            .order_by(Investigation.created_at)
        )
    )
    ids = [i.id for i in investigations]
    diagnoses = list(
        db.scalars(
            select(Diagnosis).where(Diagnosis.investigation_id.in_(ids)).order_by(Diagnosis.created_at)
        )
    )
    remediations = list(
        db.scalars(
            select(Remediation).where(Remediation.incident_id == incident.id).order_by(Remediation.created_at)
        )
    )
    result = {
        "incident": row_dict(incident),
        "investigations": [row_dict(r) for r in investigations],
        "diagnoses": [row_dict(r) for r in diagnoses],
        "remediations": [row_dict(r) for r in remediations],
    }
    for key, model, condition in [
        ("evidence", Evidence, Evidence.incident_id == incident.id),
        ("events", IncidentEvent, IncidentEvent.incident_id == incident.id),
        ("steps", InvestigationStep, InvestigationStep.investigation_id.in_(ids)),
        ("usage", LLMUsage, LLMUsage.investigation_id.in_(ids)),
        ("verifications", Verification, Verification.diagnosis_id.in_([d.id for d in diagnoses])),
        (
            "executions",
            RemediationExecution,
            RemediationExecution.remediation_id.in_([r.id for r in remediations]),
        ),
        ("approvals", ApprovalRequest, ApprovalRequest.remediation_id.in_([r.id for r in remediations])),
    ]:
        result[key] = [
            row_dict(r) for r in db.scalars(select(model).where(condition).order_by(model.created_at))
        ]
    return result


def task_detail(db, task):
    return {
        "task": row_dict(task),
        "logs": [
            row_dict(r)
            for r in db.scalars(
                select(TaskLog).where(TaskLog.task_id == task.id).order_by(TaskLog.created_at)
            )
        ],
    }


def health():
    cfg = settings()
    checks = {
        "database": probes.database_status,
        "redis": probes.redis_status,
        "experiment_broker": lambda: probes.redis_status(cfg.broker_probe_url),
        "dependency": lambda: probes.http_health(cfg.dependency_url),
        "worker_control": lambda: probes.http_health(cfg.worker_control_url),
        "broker_proxy": lambda: probes.http_health(cfg.proxy_control_url),
        "prometheus": lambda: probes.http_health(cfg.prometheus_url, "/-/healthy"),
    }
    if cfg.embedding_provider != "disabled":
        checks["retrieval"] = lambda: probes.http_health(cfg.embedding_service_url)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(checks, pool.map(lambda fn: fn(), checks.values()), strict=True))
    operational_status = "HEALTHY" if all(r["status"] == "HEALTHY" for r in results.values()) else "DEGRADED"
    results["llm"] = {
        "status": "DEGRADED" if cfg.llm_configured else "UNAVAILABLE",
        "configured": cfg.llm_configured,
        "detail": "Configuration present; connectivity is established by an actual investigation request"
        if cfg.llm_configured
        else "No provider configured; RULE_BASED remains available",
    }
    results["github"] = {
        "status": "DEGRADED" if cfg.github_repository else "UNAVAILABLE",
        "configured": bool(cfg.github_repository),
        "detail": "Optional; local source inspection is available",
    }
    return {
        "status": operational_status,
        "services": results,
    }


def metric_summary(db):
    latest = db.scalar(select(MetricSample).order_by(MetricSample.created_at.desc()).limit(1))
    counts = dict(
        db.execute(
            select(TaskExecution.status, func.count(TaskExecution.id)).group_by(TaskExecution.status)
        ).all()
    )
    return {
        "latest": {"at": latest.created_at, **latest.values} if latest else None,
        "task_counts": counts,
        "incident_count": db.scalar(select(func.count(Incident.id))) or 0,
        "recovered_incidents": db.scalar(
            select(func.count(Incident.id)).where(Incident.status == "RECOVERED")
        )
        or 0,
        "worker_count": db.scalar(select(func.count(Worker.id))) or 0,
    }
