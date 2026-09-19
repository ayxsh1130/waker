import json

from app.core.schemas import DiagnosisOutput
from app.investigation.verification import COMPONENT, worker_failure_observations


def diagnose_rule(evidence):
    text = json.dumps([e["content"] for e in evidence], default=str).lower()
    cause, action = "UNKNOWN", "REQUEST_HUMAN"
    if '"broker": {"status": "unavailable"' in text:
        cause = "BROKER_DISRUPTION"
    elif worker_failure_observations(evidence)[0]:
        cause, action = "WORKER_FAILURE", "RESTART_WORKER"
    elif "invalid literal for int" in text:
        cause = "BAD_CONFIGURATION"
    elif "zerodivisionerror" in text:
        cause = "DEPLOYMENT_REGRESSION"
    elif "operationalerror" in text:
        cause = "DATABASE_FAILURE"
    elif "readtimeout" in text or "connecttimeout" in text:
        cause, action = "API_TIMEOUT", "RETRY_TASK"
    elif '"status_code": 500' in text and '"status_code": 200' in text:
        cause, action = "INTERMITTENT_FAILURE", "RETRY_TASK"
    elif "httpstatuserror" in text:
        cause, action = "DEPENDENCY_ERROR", "RETRY_TASK"
    elif any(f'"retry_count": {n}' in text for n in range(3, 10)):
        cause, action = "POISON_TASK", "QUARANTINE_TASK"
    elif "valueerror" in text:
        cause = "TASK_EXCEPTION"
    elif any(
        v > 20
        for e in evidence
        if e["kind"] == "get_queue_metrics"
        for v in e["content"].get("depths", {}).values()
    ):
        cause, action = "OVERLOAD", "PAUSE_QUEUE"
    return DiagnosisOutput(
        root_cause=cause,
        summary=f"Deterministic rules matched {cause.lower().replace('_', ' ')} in operational evidence.",
        affected_component=COMPONENT.get(cause, "unknown"),
        confidence=0.8 if cause != "UNKNOWN" else 0.2,
        supporting_evidence=[e["id"] for e in evidence if e.get("available", True)],
        contradicting_evidence=[],
        alternative_hypotheses=[
            "Other causes may require manual investigation; rule confidence is not calibrated."
        ],
        recommended_action=action,
    )
