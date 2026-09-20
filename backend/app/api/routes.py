import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.api.queries import health, incident_detail, metric_summary, task_detail
from app.core.auth import require_session, require_write
from app.core.config import settings
from app.core.schemas import (
    Configuration,
    ExperimentInput,
    FaultInput,
    StrictModel,
    TaskInput,
    WorkloadInput,
)
from app.db.base import uid
from app.db.models import (
    ApprovalRequest,
    Experiment,
    FaultInjection,
    HistoricalIncident,
    Incident,
    Investigation,
    MetricSample,
    TaskExecution,
    Worker,
)
from app.db.session import get_db, row_dict
from app.experiments.service import create_experiment, results
from app.faults.service import schedule_fault
from app.observability.events import incident_event
from app.remediation.service import decide_approval, latest_diagnosis, propose
from app.tools.probes import queue_metrics
from app.workers.publisher import create_task

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


def required(db, model, key):
    row = db.get(model, key)
    if not row:
        raise HTTPException(404, "Record not found")
    return row


def manual_incident(db, key):
    incident = required(db, Incident, key)
    if db.scalar(
        select(FaultInjection.id).where(
            FaultInjection.scope_id == incident.scope_id, FaultInjection.run_id.is_not(None)
        )
    ):
        raise HTTPException(
            409, "Experimental trials own their investigation and results; use a separate manual fault"
        )
    return incident


def list_rows(db, model, limit=200):
    return [row_dict(r) for r in db.scalars(select(model).order_by(model.created_at.desc()).limit(limit))]


@router.get("/tasks")
def tasks(db=Depends(get_db, scope="function"), limit: int = Query(200, ge=1, le=1000)):
    return list_rows(db, TaskExecution, limit)


@router.post("/tasks", status_code=202, dependencies=[Depends(require_write)])
def add_task(body: TaskInput, db=Depends(get_db, scope="function")):
    return row_dict(create_task(db, body))


@router.get("/tasks/{key}")
def one_task(key: str, db=Depends(get_db, scope="function")):
    return task_detail(db, required(db, TaskExecution, key))


@router.post("/workload", status_code=202, dependencies=[Depends(require_write)])
def workload(body: WorkloadInput, db=Depends(get_db, scope="function")):
    names = (
        [
            "send_email",
            "process_report",
            "resize_image",
            "call_external_api",
            "process_database_record",
            "data_processing_task",
        ]
        if body.name == "mixed"
        else [body.name]
    )
    return {
        "tasks": [
            row_dict(create_task(db, TaskInput(name=names[i % len(names)], idempotency_key=uid(), count=10)))
            for i in range(body.count)
        ]
    }


@router.get("/incidents")
def incidents(db=Depends(get_db, scope="function"), limit: int = Query(200, ge=1, le=1000)):
    return list_rows(db, Incident, limit)


@router.get("/incidents/{key}")
def one_incident(key: str, db=Depends(get_db, scope="function")):
    return incident_detail(db, required(db, Incident, key))


@router.get("/incidents/{key}/evidence")
def evidence(key: str, db=Depends(get_db, scope="function")):
    return one_incident(key, db)["evidence"]


@router.get("/incidents/{key}/investigation")
def investigation(key: str, db=Depends(get_db, scope="function")):
    detail = one_incident(key, db)
    return {k: detail[k] for k in ["investigations", "steps", "diagnoses", "verifications", "usage"]}


class InvestigateInput(StrictModel):
    configuration: Configuration = Configuration.FULL_SYSTEM


@router.post("/incidents/{key}/investigate", status_code=202, dependencies=[Depends(require_write)])
def investigate(key: str, body: InvestigateInput, db=Depends(get_db, scope="function")):
    incident = manual_incident(db, key)
    db.refresh(incident, with_for_update=True)
    if incident.investigation_requested or db.scalar(
        select(Investigation.id).where(Investigation.incident_id == key, Investigation.status == "RUNNING")
    ):
        raise HTTPException(409, "Investigation already queued or running")
    incident.investigation_requested = True
    incident.requested_configuration = body.configuration.value
    incident_event(db, key, "Investigation", {"status": "QUEUED", "configuration": body.configuration.value})
    return {"status": "QUEUED"}


@router.post("/incidents/{key}/verify", dependencies=[Depends(require_write)])
def verify(key: str, db=Depends(get_db, scope="function")):
    incident = manual_incident(db, key)
    diagnosis = latest_diagnosis(db, key)
    if not diagnosis:
        raise HTTPException(409, "No diagnosis available")
    from app.investigation.service import verify_current

    return row_dict(verify_current(db, incident, diagnosis))


@router.post("/incidents/{key}/remediate", dependencies=[Depends(require_write)])
def remediate(key: str, db=Depends(get_db, scope="function")):
    incident = manual_incident(db, key)
    diagnosis = latest_diagnosis(db, key)
    if not diagnosis:
        raise HTTPException(409, "No diagnosis available")
    return row_dict(propose(db, incident, diagnosis))


@router.get("/approvals")
def approvals(db=Depends(get_db, scope="function")):
    from app.db.models import Remediation

    rows = db.execute(
        select(ApprovalRequest, Remediation)
        .join(Remediation, Remediation.id == ApprovalRequest.remediation_id)
        .order_by(ApprovalRequest.created_at.desc())
        .limit(200)
    )
    return [
        {
            **row_dict(a),
            "incident_id": r.incident_id,
            "action": r.action,
            "risk": r.risk,
            "parameters": r.parameters,
            "reason": r.reason,
        }
        for a, r in rows
    ]


@router.post("/remediations/{key}/approve")
def approve(key: str, actor=Depends(require_write), db=Depends(get_db, scope="function")):
    return row_dict(decide_approval(db, key, True, actor))


@router.post("/remediations/{key}/reject")
def reject(key: str, actor=Depends(require_write), db=Depends(get_db, scope="function")):
    return row_dict(decide_approval(db, key, False, actor))


@router.get("/faults")
def faults(db=Depends(get_db, scope="function")):
    return list_rows(db, FaultInjection)


@router.post("/faults/inject", status_code=202, dependencies=[Depends(require_write)])
def inject_fault(body: FaultInput, db=Depends(get_db, scope="function")):
    if db.scalar(select(Experiment.id).where(Experiment.status.in_(["QUEUED", "RUNNING"]))):
        raise HTTPException(409, "Wait for the active experiment")
    return row_dict(schedule_fault(db, body))


@router.post("/faults/{key}/reset", dependencies=[Depends(require_write)])
def reset(key: str, db=Depends(get_db, scope="function")):
    fault = required(db, FaultInjection, key)
    if fault.run_id:
        raise HTTPException(
            409, "Experiment controls its cleanup; restarting control interrupts and cleans the trial"
        )
    if fault.status != "RESET":
        fault.status = "RESETTING"
    return {"status": fault.status}


@router.get("/workers")
def workers(db=Depends(get_db, scope="function")):
    return list_rows(db, Worker)


@router.get("/queues")
def queues():
    return queue_metrics()


@router.get("/system/health")
def system_health():
    return health()


@router.get("/metrics/summary")
def summary(db=Depends(get_db, scope="function")):
    return metric_summary(db)


@router.get("/metrics/timeseries")
def timeseries(db=Depends(get_db, scope="function"), limit: int = Query(120, ge=1, le=1000)):
    return [{"at": r["created_at"], **r["values"]} for r in reversed(list_rows(db, MetricSample, limit))]


@router.get("/history")
def history(db=Depends(get_db, scope="function")):
    return list_rows(db, HistoricalIncident)


@router.get("/experiments")
def experiments(db=Depends(get_db, scope="function")):
    return list_rows(db, Experiment)


@router.post("/experiments/run", status_code=202, dependencies=[Depends(require_write)])
def start_experiment(body: ExperimentInput, db=Depends(get_db, scope="function")):
    return row_dict(create_experiment(db, body))


@router.get("/experiments/results")
def experiment_results(
    experiment_id: str | None = None,
    configuration: str | None = None,
    fault_type: str | None = None,
    model: str | None = None,
    since: datetime | None = None,
    db=Depends(get_db, scope="function"),
):
    return results(db, experiment_id, configuration, fault_type, model, since)


@router.get("/experiments/{key}/export")
def export(
    key: str, format: str = Query("json", pattern="^(json|csv)$"), db=Depends(get_db, scope="function")
):
    experiment = required(db, Experiment, key)
    data = {"experiment": row_dict(experiment), **results(db, key)}
    if format == "json":
        return Response(
            json.dumps(jsonable_encoder(data), indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="experiment-{experiment.id}.json"'},
        )
    rows = [
        {
            **{
                k: r[k]
                for k in [
                    "id",
                    "configuration",
                    "fault_type",
                    "trial",
                    "status",
                    "error",
                    "started_at",
                    "completed_at",
                ]
            },
            "metadata": json.dumps(r["metadata_snapshot"]),
            **(r["metrics"] or {}),
        }
        for r in data["runs"]
    ]
    fields = list(dict.fromkeys(k for r in rows for k in r))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v)
                for k, v in row.items()
            }
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="experiment-{experiment.id}.csv"'},
    )


@router.get("/experiments/{key}")
def experiment_detail(key: str, db=Depends(get_db, scope="function")):
    return {"experiment": row_dict(required(db, Experiment, key)), **results(db, key)}


@router.get("/settings")
def configuration():
    cfg = settings()
    keys = [
        "app_environment",
        "app_auth_enabled",
        "remediation_mode",
        "remediation_min_confidence",
        "remediation_cooldown_seconds",
        "observation_seconds",
        "auto_investigate",
        "faults_enabled",
        "llm_provider",
        "llm_temperature",
        "agent_max_steps",
        "agent_max_tool_calls",
        "agent_max_reinvestigations",
        "agent_prompt_version",
        "workflow_version",
        "embedding_provider",
        "embedding_model",
        "software_revision",
    ]
    return {
        **{k: getattr(cfg, k) for k in keys},
        "model": cfg.model_name,
        "llm_configured": cfg.llm_configured,
        "github_configured": bool(cfg.github_repository),
    }
