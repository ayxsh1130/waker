import logging
import time

from sqlalchemy import select

from app.agents.workflow import InvestigationRunner, create_investigation
from app.core.config import settings
from app.core.schemas import FaultInput
from app.db.base import now
from app.db.models import (
    Experiment,
    ExperimentMetric,
    ExperimentRun,
    FaultInjection,
    Incident,
    LLMUsage,
    RemediationExecution,
    RuntimeSetting,
    TaskExecution,
    Verification,
    Worker,
)
from app.db.session import session_scope
from app.experiments.metrics import evaluate_run
from app.faults.service import reset_fault, schedule_fault
from app.remediation.service import propose
from app.tools.probes import redis_status

logger = logging.getLogger(__name__)


def run_next(stop):
    with session_scope() as db:
        experiment = db.scalar(
            select(Experiment)
            .where(Experiment.status.in_(["QUEUED", "RUNNING"]))
            .order_by(Experiment.created_at)
            .limit(1)
        )
        if not experiment:
            return
        run = db.scalar(
            select(ExperimentRun)
            .where(ExperimentRun.experiment_id == experiment.id, ExperimentRun.status == "QUEUED")
            .order_by(ExperimentRun.created_at)
            .limit(1)
        )
        if not run:
            experiment.status = "COMPLETED"
            experiment.completed_at = now()
            return
        if (
            run.configuration != "RULE_BASED"
            and not settings().llm_configured
            and run.fault_type != "HEALTHY"
        ):
            run.status = "SKIPPED"
            run.error = "Selected LLM provider is not configured"
            run.completed_at = now()
            return
        if db.scalar(
            select(FaultInjection.id).where(FaultInjection.status.in_(["ACTIVE", "SCHEDULED", "RESETTING"]))
        ):
            return
        # Isolate trials: do not inject while an earlier experimental task is outstanding.
        outstanding = db.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.queue == "experiment",
                TaskExecution.status.in_(["PENDING", "QUEUED", "STARTED", "RETRYING", "LOST"]),
            )
            .order_by(TaskExecution.created_at)
            .limit(1)
        )
        if outstanding:
            from app.db.base import seconds

            if seconds(now(), outstanding.created_at) > 180:
                run.status = "BLOCKED"
                run.error = "Experimental queue did not drain; inspect outstanding task " + outstanding.id
                run.completed_at = now()
                experiment.status = "BLOCKED"
                experiment.completed_at = now()
            return
        worker = db.scalar(select(Worker).where(Worker.name == "experiment@autopilot"))
        from app.db.base import seconds

        if (
            not worker
            or worker.status != "ONLINE"
            or seconds(now(), worker.last_heartbeat) > settings().worker_offline_seconds
            or redis_status(settings().broker_probe_url)["status"] != "HEALTHY"
        ):
            key = "experiment_wait:" + run.id
            waiting = db.get(RuntimeSetting, key)
            if not waiting:
                waiting = RuntimeSetting(key=key, value={"since": now().isoformat()})
                db.add(waiting)
                db.flush()
            run.error = "Waiting for a healthy experimental worker and broker"
            if seconds(now(), waiting.updated_at) > 180:
                run.status = "BLOCKED"
                run.completed_at = now()
                experiment.status = "BLOCKED"
                experiment.completed_at = now()
            return
        experiment.status = "RUNNING"
        run.status = "RUNNING"
        run.error = None
        run.started_at = now()
        fault = schedule_fault(db, FaultInput(fault_type=run.fault_type, **run.parameters), run.id)
        run_id, fault_id, cutoff, history = (
            run.id,
            fault.id,
            experiment.created_at,
            experiment.history_snapshot,
        )
    incident_id = None
    diagnosis = None
    investigation = None
    execution = None
    healthy_completed = False
    try:
        deadline = time.monotonic() + 100
        while not stop.wait(1) and time.monotonic() < deadline:
            with session_scope() as db:
                run = db.get(ExperimentRun, run_id)
                fault = db.get(FaultInjection, fault_id)
                incident = db.scalar(
                    select(Incident)
                    .where(Incident.scope_id == fault.scope_id)
                    .order_by(Incident.created_at)
                    .limit(1)
                )
                tasks = list(
                    db.scalars(select(TaskExecution).where(TaskExecution.scope_id == fault.scope_id))
                )
                elapsed = time.monotonic() - (deadline - 100)
                if (
                    run.fault_type == "HEALTHY"
                    and elapsed >= 20
                    and tasks
                    and all(t.status in {"SUCCEEDED", "FAILED", "QUARANTINED", "UNCERTAIN"} for t in tasks)
                ):
                    healthy_completed = True
                    incident_id = incident.id if incident else None
                    break
                if (
                    run.fault_type != "HEALTHY"
                    and incident
                    and (
                        elapsed >= 28
                        or any(t.status in {"FAILED", "QUARANTINED", "UNCERTAIN", "LOST"} for t in tasks)
                    )
                ):
                    incident_id = incident.id
                    break
        if stop.is_set():
            raise RuntimeError("Controller stopped during trial")
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            fault = db.get(FaultInjection, fault_id)
            incident = db.get(Incident, incident_id) if incident_id else None
            if incident and run.fault_type != "HEALTHY":
                investigation = create_investigation(db, incident, run.configuration, cutoff)
                run.investigation_id = investigation.id
                run.incident_id = incident.id
                db.commit()
                diagnosis = InvestigationRunner(db, incident, investigation, history_ids=history).run()
                if diagnosis and run.configuration == "FULL_SYSTEM":
                    remediation = propose(db, incident, diagnosis)
                    db.commit()
                    if remediation.requested:
                        action_deadline = time.monotonic() + settings().observation_seconds + 30
                        while not stop.wait(1) and time.monotonic() < action_deadline:
                            db.expire_all()
                            execution = db.scalar(
                                select(RemediationExecution).where(
                                    RemediationExecution.remediation_id == remediation.id
                                )
                            )
                            if execution and execution.status not in {"EXECUTING", "OBSERVING"}:
                                break
            elif incident:
                run.incident_id = incident.id
            verification = (
                db.scalar(
                    select(Verification)
                    .where(Verification.diagnosis_id == diagnosis.id)
                    .order_by(Verification.created_at.desc())
                    .limit(1)
                )
                if diagnosis
                else None
            )
            task = db.get(TaskExecution, incident.task_id) if incident and incident.task_id else None
            usage = (
                list(db.scalars(select(LLMUsage).where(LLMUsage.investigation_id == investigation.id)))
                if investigation
                else []
            )
            if incident:
                db.refresh(incident)
            db.refresh(fault)
            if fault.fault_type == "HEALTHY" and not healthy_completed:
                run.status = "FAILED"
                run.error = (
                    "Healthy workload did not complete in the observation window; excluded from metrics"
                )
            elif fault.fault_type == "WORKER_FAILURE" and not fault.parameters.get("crash_executed"):
                run.status = "FAILED"
                run.error = "Worker crash was not observed; trial excluded from metrics"
            elif not fault.injected_at:
                run.status = "FAILED"
                run.error = "Fault never activated; trial excluded from metrics"
            else:
                values = evaluate_run(
                    run.ground_truth,
                    fault.injected_at,
                    incident,
                    investigation,
                    diagnosis,
                    verification,
                    execution,
                    task,
                    usage,
                )
                db.add(ExperimentMetric(run_id=run.id, values=values))
                run.status = (
                    "COMPLETED" if not investigation or investigation.status == "COMPLETED" else "FAILED"
                )
                run.error = investigation.error if investigation else None
            run.completed_at = now()
            db.commit()
            reset_fault(db, fault)
    except Exception as exc:
        logger.exception("Experiment trial failed", extra={"experiment_run_id": run_id})
        with session_scope() as db:
            run = db.get(ExperimentRun, run_id)
            run.status = "INTERRUPTED" if stop.is_set() else "FAILED"
            # Persist actionable error context without storing provider payloads or secrets.
            run.error = ("Trial stopped: " if stop.is_set() else "Trial failed: ") + type(exc).__name__ + ": " + str(exc)[:500]
            run.completed_at = now()
            reset_fault(db, db.get(FaultInjection, fault_id))
