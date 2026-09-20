"""Dedicated control plane stays available when a task worker or its broker link fails."""

import logging
import signal
import threading

from sqlalchemy import select, text

from app.agents.workflow import InvestigationRunner, create_investigation
from app.db.base import now
from app.db.models import (
    Experiment,
    ExperimentRun,
    FaultInjection,
    Incident,
    Investigation,
    Remediation,
    RemediationExecution,
)
from app.db.session import engine, session_scope
from app.experiments.runner import run_next
from app.faults.service import tick_faults
from app.observability.monitor import event_loop, sample
from app.observability.telemetry import configure_telemetry
from app.remediation.recovery import observe_recoveries
from app.remediation.service import execute_remediation, expire_approvals, propose
from app.retrieval.client import index_pending
from app.workers.publisher import dispatch_pending

log = logging.getLogger(__name__)


def investigate_next(db):
    incident = db.scalar(
        select(Incident)
        .where(Incident.investigation_requested.is_(True))
        .order_by(Incident.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not incident:
        return
    incident.investigation_requested = False
    investigation = create_investigation(db, incident, incident.requested_configuration)
    db.commit()
    diagnosis = InvestigationRunner(db, incident, investigation).run()
    if diagnosis and investigation.configuration == "FULL_SYSTEM":
        propose(db, incident, diagnosis)


def remedies(db):
    expire_approvals(db)
    for remediation in list(db.scalars(select(Remediation).where(Remediation.requested.is_(True)))):
        execute_remediation(db, remediation)
    observe_recoveries(db)


def recover_interrupted(db):
    for inv in db.scalars(select(Investigation).where(Investigation.status == "RUNNING")):
        inv.status = "INTERRUPTED"
        inv.error = "Controller restarted; rerun investigation"
        inv.completed_at = now()
        db.get(Incident, inv.incident_id).status = "INTERRUPTED"
    for execution in db.scalars(
        select(RemediationExecution).where(RemediationExecution.status == "EXECUTING")
    ):
        execution.status = "UNCERTAIN"
        execution.completed_at = now()
        execution.result = {"reason": "Controller restarted during action; no automatic replay"}
        remediation = db.get(Remediation, execution.remediation_id)
        remediation.status = "UNCERTAIN"
        db.get(Incident, remediation.incident_id).status = "HUMAN_REVIEW"
    for run in db.scalars(select(ExperimentRun).where(ExperimentRun.status == "RUNNING")):
        run.status = "INTERRUPTED"
        run.completed_at = now()
        run.error = "Controller restarted"
        experiment = db.get(Experiment, run.experiment_id)
        experiment.status = "INTERRUPTED"
        experiment.completed_at = now()
    for fault in db.scalars(select(FaultInjection).where(FaultInjection.status.in_(["ACTIVE", "SCHEDULED"]))):
        fault.status = "RESETTING"


def main():
    configure_telemetry("autopilot-control")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    lock = engine().connect()
    if lock.dialect.name == "postgresql" and not lock.scalar(text("SELECT pg_try_advisory_lock(19042027)")):
        raise RuntimeError("Another control plane already holds the singleton lock")
    with session_scope() as db:
        recover_interrupted(db)

    def loop(fn, interval):
        while not stop.is_set():
            try:
                with session_scope() as db:
                    fn(db)
            except Exception as exc:
                log.error("Control loop %s failed: %s", fn.__name__, type(exc).__name__)
            stop.wait(interval)

    threads = [
        threading.Thread(target=loop, args=(fn, period), daemon=True, name=fn.__name__)
        for fn, period in [
            (dispatch_pending, 1),
            (tick_faults, 1),
            (sample, 5),
            (investigate_next, 2),
            (remedies, 1),
            (index_pending, 15),
        ]
    ]

    def experiments():
        while not stop.is_set():
            try:
                run_next(stop)
            except Exception as exc:
                log.error("Experiment runner failed: %s", type(exc).__name__)
            stop.wait(2)

    threads.extend(
        [
            threading.Thread(target=event_loop, args=(stop,), daemon=True),
            threading.Thread(target=experiments, daemon=True),
        ]
    )
    for thread in threads:
        thread.start()
    try:
        while not stop.wait(1):
            lock.execute(text("SELECT 1"))
            lock.commit()
    except Exception:
        stop.set()
        raise
    finally:
        lock.close()


if __name__ == "__main__":
    main()
