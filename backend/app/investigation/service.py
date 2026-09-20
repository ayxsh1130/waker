"""Refresh operational evidence without changing the model's diagnosis or citations."""

from sqlalchemy import select

from app.agents.workflow import evidence_view
from app.core.schemas import DiagnosisOutput, ToolArguments
from app.db.models import Evidence, Investigation, Verification
from app.investigation.verification import verify_diagnosis
from app.observability.events import incident_event
from app.tools.registry import DiagnosticTools


def verify_current(db, incident, diagnosis):
    output = DiagnosisOutput.model_validate(
        {key: getattr(diagnosis, key) for key in DiagnosisOutput.model_fields}
    )
    rows = list(
        db.scalars(
            select(Evidence).where(
                Evidence.incident_id == incident.id,
                (Evidence.investigation_id == diagnosis.investigation_id)
                | (Evidence.kind == "initial_signal"),
            )
        )
    )
    inv = db.get(Investigation, diagnosis.investigation_id)
    tools = DiagnosticTools(db, incident, inv)
    checks = {
        "WORKER_FAILURE": ("get_worker_status", "get_redis_status"),
        "HEALTHY": ("get_worker_status", "get_redis_status"),
        "BROKER_DISRUPTION": ("get_redis_status",),
        "API_TIMEOUT": ("get_external_service_status",),
        "DEPENDENCY_ERROR": ("get_external_service_status",),
        "INTERMITTENT_FAILURE": ("get_external_service_status",),
        "DATABASE_FAILURE": ("get_database_status",),
        "OVERLOAD": ("get_worker_status", "get_queue_metrics"),
    }.get(diagnosis.root_cause, ())
    fresh = []
    for name in checks:
        if name in tools.allowed:
            row = tools.run(name, ToolArguments())
            row.retrieval_method = "verification_check"
            fresh.append(row)
    # Additional verifier observations are recorded explicitly. Never silently
    # rewrite the diagnosis or remove invalid/contradicting model references.
    output = output.model_copy(
        update={
            "supporting_evidence": list(dict.fromkeys(output.supporting_evidence + [r.id for r in fresh]))
        }
    )
    result = verify_diagnosis(output, [evidence_view(r) for r in rows + fresh])
    previous = db.scalar(
        select(Verification)
        .where(Verification.diagnosis_id == diagnosis.id)
        .order_by(Verification.created_at.desc())
        .limit(1)
    )
    if (
        not fresh
        and previous
        and all(getattr(previous, key) == value for key, value in result.model_dump().items())
    ):
        return previous
    record = Verification(diagnosis_id=diagnosis.id, **result.model_dump())
    db.add(record)
    db.flush()
    # Rechecking a diagnosis must not erase a later lifecycle stage.
    if incident.status not in {
        "APPROVAL_REQUIRED",
        "REMEDIATING",
        "OBSERVING",
        "RECOVERED",
        "CLOSED",
        "PARTIALLY_RECOVERED",
        "HUMAN_REVIEW",
    }:
        incident.status = "VERIFIED" if result.verified else "UNVERIFIED"
    incident_event(
        db,
        incident.id,
        "Verification",
        {
            **result.model_dump(),
            "verification_id": record.id,
            "verifier_evidence_ids": [row.id for row in fresh],
        },
    )
    db.flush()
    return record
