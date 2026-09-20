"""Only operational capabilities. Fault and evaluation models are deliberately absent."""

from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.safety import redact
from app.db.base import now, seconds
from app.db.models import (
    DeploymentRecord,
    Evidence,
    MetricSample,
    Remediation,
    RemediationExecution,
    TaskExecution,
    TaskLog,
    Worker,
)
from app.tools import probes, source

TOOLS = {
    "get_incident_details": "Read incident summary and observed symptoms.",
    "get_task_details": "Read task status, retries, exception and stack trace.",
    "get_task_logs": "Read logs of a related task.",
    "get_worker_logs": "Read task logs associated with the observed worker.",
    "get_worker_status": "Inspect worker heartbeats, activity and queues.",
    "get_queue_metrics": "Measure Redis queue depths.",
    "get_worker_metrics": "Inspect observed Celery worker statistics.",
    "get_system_metrics": "Read recent operational measurements.",
    "get_recent_errors": "Inspect errors in this incident scope.",
    "get_trace": "Read correlated task logs and trace identifiers.",
    "get_related_tasks": "Inspect other tasks in this incident scope.",
    "get_redis_status": "Probe broker and control Redis connectivity.",
    "get_database_status": "Probe PostgreSQL connectivity.",
    "get_external_service_status": "Make bounded HTTP probes and read observed dependency responses.",
    "get_recent_deployments": "Read release metadata for the workload.",
    "get_recent_git_commits": "Read local or GitHub commit metadata.",
    "get_source_file": "Read a bounded code region inside the approved repository.",
    "search_source_code": "Search approved code for a literal string.",
    "search_historical_incidents": "Retrieve recovered incidents. Similarity is not proof.",
    "get_previous_remediation_results": "Read previous remediation outcomes for this incident.",
}
RAG_CONFIGS = {"LLM_RAG", "AGENT_RAG", "FULL_SYSTEM"}
DYNAMIC_CONFIGS = {"AGENT_TOOLS", "AGENT_RAG", "FULL_SYSTEM"}


def incident_projection(incident):
    return redact(
        {
            k: getattr(incident, k)
            for k in [
                "id",
                "title",
                "description",
                "component",
                "symptoms",
                "task_id",
                "worker",
                "queue",
                "trace_id",
                "correlation_id",
            ]
        }
    )


def task_projection(task):
    return redact(
        {
            k: getattr(task, k)
            for k in [
                "id",
                "name",
                "status",
                "queue",
                "worker",
                "retry_count",
                "idempotent",
                "quarantined",
                "exception",
                "stack_trace",
                "correlation_id",
                "trace_id",
                "started_at",
                "completed_at",
            ]
        }
    )


class DiagnosticTools:
    def __init__(self, db, incident, investigation, history_ids=None):
        self.db, self.incident, self.investigation, self.history_ids = (
            db,
            incident,
            investigation,
            history_ids,
        )

    @property
    def allowed(self):
        config = self.investigation.configuration
        if config not in DYNAMIC_CONFIGS and config != "RULE_BASED":
            return {}
        return {k: v for k, v in TOOLS.items() if k != "search_historical_incidents" or config in RAG_CONFIGS}

    def run(self, name, args, internal_baseline_rag=False):
        if name not in self.allowed and not (
            internal_baseline_rag
            and name == "search_historical_incidents"
            and self.investigation.configuration in RAG_CONFIGS
        ):
            raise ValueError("Tool unavailable in this configuration")
        try:
            content, component = self._call(name, args)
            content = redact(content)
            available = content.get("available", True) is not False
        except Exception as exc:
            content, component, available = (
                {"error_type": type(exc).__name__, "message": "Evidence source unavailable"},
                "unknown",
                False,
            )
        evidence = Evidence(
            incident_id=self.incident.id,
            investigation_id=self.investigation.id,
            kind=name,
            source=name,
            component=component,
            retrieval_method="diagnostic_tool",
            content=content,
            available=available,
        )
        self.db.add(evidence)
        self.db.flush()
        return evidence

    def _call(self, name, args):
        db, incident = self.db, self.incident
        task_id = args.task_id or incident.task_id
        task = db.get(TaskExecution, task_id, populate_existing=True) if task_id else None
        if task and task.scope_id != incident.scope_id:
            raise ValueError("Task outside investigation scope")
        if name == "get_incident_details":
            return incident_projection(incident), incident.component
        if name == "get_task_details":
            return task_projection(task) if task else {"available": False}, "task"
        tasks = list(
            db.scalars(
                select(TaskExecution)
                .where(TaskExecution.scope_id == incident.scope_id)
                .order_by(TaskExecution.created_at.desc())
                .limit(50)
            )
        )
        if name == "get_related_tasks":
            return {"tasks": [task_projection(t) for t in tasks[: args.limit]]}, "task"
        if name in {"get_task_logs", "get_worker_logs", "get_recent_errors", "get_trace"}:
            ids = [task.id] if name in {"get_task_logs", "get_trace"} and task else [t.id for t in tasks]
            if name == "get_worker_logs":
                ids = [t.id for t in tasks if t.worker == (args.worker or incident.worker)]
            query = select(TaskLog).where(TaskLog.task_id.in_(ids)).order_by(TaskLog.created_at.desc())
            if name == "get_recent_errors":
                query = query.where(TaskLog.level == "ERROR")
            logs = db.scalars(query.limit(args.limit)).all()
            return {
                "logs": [
                    {
                        "id": r.id,
                        "task_id": r.task_id,
                        "level": r.level,
                        "message": r.message,
                        "data": r.data,
                        "timestamp": r.created_at.isoformat(),
                    }
                    for r in logs
                ],
                "trace_id": task.trace_id if task else incident.trace_id,
            }, "task"
        if name in {"get_worker_status", "get_worker_metrics"}:
            target = args.worker or incident.worker
            if args.worker and incident.worker and args.worker != incident.worker:
                raise ValueError("Worker outside investigation scope")
            query = select(Worker).execution_options(populate_existing=True)
            if target:
                query = query.where(Worker.name == target)
            workers = db.scalars(query).all()
            observed_at = now()
            return {
                "target_worker": target,
                "observed_at": observed_at.isoformat(),
                "offline_threshold_seconds": settings().worker_offline_seconds,
                "workers": [
                    {
                        "name": w.name,
                        "status": (
                            "OFFLINE"
                            if seconds(observed_at, w.last_heartbeat) > settings().worker_offline_seconds
                            else w.status
                        ),
                        "heartbeat_age_seconds": seconds(observed_at, w.last_heartbeat),
                        "last_heartbeat": w.last_heartbeat.isoformat(),
                        "active_count": len(w.active),
                        "reserved_count": len(w.reserved),
                        "scheduled_count": len(w.scheduled),
                        "queues": w.queues,
                        "stats": w.stats,
                    }
                    for w in workers
                ],
            }, "worker"
        if name == "get_queue_metrics":
            return probes.queue_metrics(), "queue"
        if name == "get_system_metrics":
            samples = db.scalars(
                select(MetricSample)
                .where(MetricSample.created_at >= now() - timedelta(minutes=5))
                .order_by(MetricSample.created_at.desc())
                .limit(20)
            )
            return {"samples": [{"at": r.created_at, **r.values} for r in samples]}, "system"
        if name == "get_redis_status":
            return {
                "control": probes.redis_status(),
                "broker": probes.redis_status(
                    settings().broker_probe_url if incident.queue == "experiment" else None
                ),
            }, "broker"
        if name == "get_database_status":
            return probes.database_status(), "database"
        if name == "get_external_service_status":
            return probes.external_status(incident.scope_id), "dependency"
        if name == "get_recent_deployments":
            releases = db.scalars(
                select(DeploymentRecord)
                .where(DeploymentRecord.scope_id == incident.scope_id)
                .order_by(DeploymentRecord.created_at.desc())
                .limit(8)
            )
            return {
                "deployments": [
                    {
                        "version": r.version,
                        "changed_files": r.changed_files,
                        "commit_hash": r.commit_hash,
                        "description": r.description,
                        "timestamp": r.created_at,
                    }
                    for r in releases
                ]
            }, "deployment"
        if name == "get_recent_git_commits":
            return source.recent_commits(), "deployment"
        if name == "get_source_file":
            return source.read_source(args.path or "", args.start_line, args.limit), "deployment"
        if name == "search_source_code":
            return source.search_source(args.query or "", args.limit), "deployment"
        if name == "search_historical_incidents":
            from app.retrieval.client import search_history

            result = search_history(incident, self.investigation.history_cutoff, self.history_ids)
            self.investigation.retrieval_snapshot = [r["id"] for r in result.get("matches", [])]
            return result, "history"
        if name == "get_previous_remediation_results":
            rows = db.execute(
                select(Remediation, RemediationExecution)
                .join(RemediationExecution)
                .where(Remediation.incident_id == incident.id)
            )
            return {
                "results": [
                    {"action": r.action, "status": e.status, "post_result": e.post_result} for r, e in rows
                ]
            }, incident.component
        raise ValueError("Unknown tool")
