import json
from datetime import UTC, datetime

from pydantic import Field

from app.core.schemas import StrictModel


class VerificationResult(StrictModel):
    verified: bool
    verification_score: float = Field(ge=0, le=1)
    supporting_checks: list[str]
    contradictions: list[str]
    missing_evidence: list[str]


COMPONENT = {
    "API_TIMEOUT": "dependency",
    "DEPENDENCY_ERROR": "dependency",
    "INTERMITTENT_FAILURE": "dependency",
    "WORKER_FAILURE": "worker",
    "TASK_EXCEPTION": "task",
    "POISON_TASK": "task",
    "BROKER_DISRUPTION": "broker",
    "DATABASE_FAILURE": "database",
    "OVERLOAD": "queue",
    "BAD_CONFIGURATION": "configuration",
    "DEPLOYMENT_REGRESSION": "deployment",
    "UNKNOWN": "unknown",
}


def evidence_timestamp(row):
    try:
        stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def latest_observation(evidence, kind):
    rows = [row for row in evidence if row.get("kind") == kind]
    if not rows or any(evidence_timestamp(row) is None for row in rows):
        return None
    row = max(rows, key=evidence_timestamp)
    age = (datetime.now(UTC) - evidence_timestamp(row)).total_seconds()
    return row if row.get("available", True) and -5 <= age <= 120 else None


def broker_status(evidence):
    row = latest_observation(evidence, "get_redis_status")
    return row["content"].get("broker", {}).get("status") if row else None


def verify_worker_recovery(diagnosis, evidence):
    """Verify recovery at observation time, without erasing the original alert."""

    def timestamp(row):
        try:
            value = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            return value.timestamp() if value.tzinfo else None
        except (KeyError, ValueError, TypeError, AttributeError):
            return None

    alerts = [
        row
        for row in evidence
        if row["kind"] == "initial_signal"
        and row["content"].get("signal") == "heartbeat_lost"
        and row["content"].get("status") == "OFFLINE"
    ]
    names = {row["content"].get("worker") for row in alerts}
    if len(names) != 1 or not next(iter(names), None):
        return False, set()
    worker = next(iter(names))
    cited = set(diagnosis.supporting_evidence)
    latest = {}
    for kind in ("get_worker_status", "get_redis_status"):
        row = latest_observation(evidence, kind)
        if row is None:
            return False, set()
        if row["id"] not in cited or not row.get("available", True):
            return False, set()
        latest[kind] = row
    worker_row = latest["get_worker_status"]
    broker_row = latest["get_redis_status"]
    workers = [row for row in worker_row["content"].get("workers", []) if row.get("name") == worker]
    if len(workers) != 1:
        return False, set()
    age = workers[0].get("heartbeat_age_seconds")
    if (
        workers[0].get("status") != "ONLINE"
        or isinstance(age, bool)
        or not isinstance(age, (int, float))
        or not 0 <= age <= 60
        or broker_row["content"].get("broker", {}).get("status") != "HEALTHY"
        or abs(timestamp(worker_row) - timestamp(broker_row)) > 120
        or diagnosis.recommended_action.value != "NO_ACTION"
    ):
        return False, set()
    resolved = {
        row["id"]
        for row in alerts
        if timestamp(row) is not None and timestamp(row) < min(timestamp(worker_row), timestamp(broker_row))
    }
    return len(resolved) == len(alerts), resolved


def verify_diagnosis(diagnosis, evidence):
    by_id = {e["id"]: e for e in evidence}
    missing = []
    contradictions = []
    checks = []
    cited = [by_id[eid] for eid in diagnosis.supporting_evidence if eid in by_id]
    for eid in diagnosis.supporting_evidence + diagnosis.contradicting_evidence:
        if eid not in by_id:
            contradictions.append("Uncollected evidence reference: " + eid)
    current = [e for e in cited if e.get("available", True) and e["kind"] != "search_historical_incidents"]
    live_kinds = {
        "get_worker_status", "get_redis_status", "get_external_service_status",
        "get_database_status", "get_queue_metrics",
    }
    latest_live = {kind: latest_observation(evidence, kind) for kind in live_kinds}
    current = [
        row for row in current
        if row["kind"] not in live_kinds
        or (latest_live[row["kind"]] and latest_live[row["kind"]]["id"] == row["id"])
    ]
    kinds = {e["kind"] for e in current}
    text = json.dumps([e["content"] for e in current], default=str).lower()
    task_text = json.dumps([
        e["content"] for e in current
        if e["kind"] in {"initial_signal", "get_task_details", "get_task_logs",
                         "get_worker_logs", "get_recent_errors", "get_trace", "get_related_tasks"}
    ], default=str).lower()
    responses = [
        response for e in current if e["kind"] == "get_external_service_status"
        for response in e["content"].get("probes", []) + e["content"].get("observations", [])
        if isinstance(response, dict)
    ]
    codes = {response.get("status_code") for response in responses
             if isinstance(response.get("status_code"), int)}
    if len(kinds) < 2:
        missing.append("At least two current evidence source types")
    if not any(k.startswith("get_") for k in kinds):
        missing.append("An operational diagnostic check")
    cause = diagnosis.root_cause.value
    healthy_worker = cause == "HEALTHY" and diagnosis.affected_component == "worker"
    recovery_ok, resolved = verify_worker_recovery(diagnosis, evidence) if healthy_worker else (False, set())
    failure_ok, failure_ids = (
        worker_failure_observations(evidence) if cause == "WORKER_FAILURE" else (False, set())
    )
    if not healthy_worker and COMPONENT.get(cause, "unknown") != diagnosis.affected_component:
        contradictions.append("Affected component does not match cause")
    if set(diagnosis.contradicting_evidence) - (resolved if recovery_ok else set()):
        contradictions.append("Unresolved contradicting evidence")
    required = {
        "HEALTHY": (
            healthy_worker and recovery_ok,
            "Same worker online with fresh heartbeat and contemporaneous healthy broker "
            "after the original alert; cite get_worker_status and get_redis_status; use NO_ACTION",
        ),
        "API_TIMEOUT": (
            any(t in task_text for t in ["readtimeout", "connecttimeout", "timeoutexceeded"])
            and "get_external_service_status" in kinds,
            "Timeout error and dependency probe",
        ),
        "DEPENDENCY_ERROR": (
            ("httpstatuserror" in task_text or any(500 <= code < 600 for code in codes))
            and "get_external_service_status" in kinds,
            "HTTP error and dependency probe",
        ),
        "WORKER_FAILURE": (
            failure_ok and failure_ids.issubset(set(diagnosis.supporting_evidence)),
            "Same worker offline and healthy broker in latest cited checks within 120 seconds; "
            "collect and cite get_worker_status and get_redis_status",
        ),
        "BROKER_DISRUPTION": (
            broker_status(evidence) == "UNAVAILABLE"
            and latest_observation(evidence, "get_redis_status")["id"] in diagnosis.supporting_evidence,
            "Failed broker probe",
        ),
        "DATABASE_FAILURE": (
            "operationalerror" in task_text and "get_database_status" in kinds,
            "Database exception and connectivity check",
        ),
        "TASK_EXCEPTION": (
            "valueerror" in task_text and "get_task_logs" in kinds,
            "Application exception in task logs",
        ),
        "POISON_TASK": (
            any(f'"retry_count": {n}' in text for n in range(3, 10)) and "get_related_tasks" in kinds,
            "Repeated failures and related task check",
        ),
        "BAD_CONFIGURATION": (
            "invalid literal for int" in task_text and "get_source_file" in kinds,
            "Parsing error and source context",
        ),
        "DEPLOYMENT_REGRESSION": (
            "zerodivisionerror" in task_text and "get_recent_deployments" in kinds and "get_source_file" in kinds,
            "Failure, release metadata and source context",
        ),
        "INTERMITTENT_FAILURE": (
            "get_external_service_status" in kinds
            and 500 in codes
            and 200 in codes,
            "Both successful and failed dependency responses",
        ),
        "OVERLOAD": (
            "get_queue_metrics" in kinds
            and "get_worker_status" in kinds
            and any(
                v > 0
                for e in current
                if e["kind"] == "get_queue_metrics"
                for v in e["content"].get("depths", {}).values()
            ),
            "Measured backlog and worker check",
        ),
    }
    ok, description = required.get(cause, (False, "Specific supported root cause required"))
    (checks if ok else missing).append(description)
    if cause == "WORKER_FAILURE" and not ok:
        for kind in ("get_worker_status", "get_redis_status"):
            row = latest_live[kind]
            if row is None:
                missing.append(kind + ": latest observation unavailable, invalid, or older than 120 seconds")
            elif row["id"] not in diagnosis.supporting_evidence:
                missing.append(kind + ": cite latest evidence " + row["id"])
        row = latest_live["get_worker_status"]
        if row:
            target = row["content"].get("target_worker")
            for worker in row["content"].get("workers", []):
                if worker.get("name") == target and worker.get("status") != "OFFLINE":
                    missing.append(f"Worker {target} is {worker.get('status')}; restart requires OFFLINE")
        row = latest_live["get_redis_status"]
        if row and row["content"].get("broker", {}).get("status") != "HEALTHY":
            missing.append("Broker is not healthy; distinguish connectivity loss from worker failure")
    if len(kinds) >= 2:
        checks.append("Multiple current source types collected")
    if cited and len(cited) == len(diagnosis.supporting_evidence):
        checks.append("Supporting references are collected evidence")
    score = len(checks) / max(1, len(checks) + len(missing) + len(contradictions))
    return VerificationResult(
        verified=not missing and not contradictions,
        verification_score=round(score, 4),
        supporting_checks=checks,
        contradictions=contradictions,
        missing_evidence=missing,
    )


def worker_failure_observations(evidence):
    """Require fresh worker and broker evidence before recommending restart."""
    latest = {}

    for kind in ("get_worker_status", "get_redis_status"):
        rows = [row for row in evidence if row["kind"] == kind]
        if not rows:
            return False, set()

        dated = []
        for row in rows:
            try:
                stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    return False, set()
            except (KeyError, ValueError, TypeError, AttributeError):
                return False, set()
            dated.append((stamp, row))

        stamp, row = max(dated, key=lambda pair: pair[0])
        age = (datetime.now(UTC) - stamp).total_seconds()
        if not row.get("available", True) or not -5 <= age <= 120:
            return False, set()

        latest[kind] = (stamp, row)

    worker_at, worker_row = latest["get_worker_status"]
    broker_at, broker_row = latest["get_redis_status"]

    target = worker_row["content"].get("target_worker")
    names = {
        row["content"].get("worker")
        for row in evidence
        if row["kind"] in {"initial_signal", "get_task_details"} and row["content"].get("worker")
    }
    if target and names and names != {target}:
        return False, set()
    if not target:
        names = {
            row["content"].get("worker")
            for row in evidence
            if row["kind"] in {"initial_signal", "get_task_details"} and row["content"].get("worker")
        }
        target = next(iter(names)) if len(names) == 1 else None

    workers = [worker for worker in worker_row["content"].get("workers", []) if worker.get("name") == target]

    if (
        not target
        or len(workers) != 1
        or workers[0].get("status") != "OFFLINE"
        or broker_row["content"].get("broker", {}).get("status") != "HEALTHY"
        or abs((worker_at - broker_at).total_seconds()) > 120
    ):
        return False, set()

    return True, {worker_row["id"], broker_row["id"]}
