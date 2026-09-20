from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.base import now
from app.db.models import ApprovalRequest, RemediationExecution
from app.remediation.policy import PolicyContext, evaluate
from app.remediation.recovery import classify_recovery
from app.remediation.service import decide_approval, execute_remediation, propose

BASE = PolicyContext(True, 0.9, 0.75, True, "FAILED", False, "experiment@autopilot", "experiment")


@pytest.mark.parametrize(
    "action,changes,expected",
    [
        ("RETRY_TASK", {}, "ALLOW"),
        ("RETRY_TASK", {"idempotent": False}, "DENIED"),
        ("RETRY_TASK", {"quarantined": True}, "DENIED"),
        ("RETRY_TASK", {"task_status": "STARTED"}, "DENIED"),
        ("RETRY_TASK", {"verified": False}, "DENIED"),
        ("RETRY_TASK", {"confidence": 0.5}, "DENIED"),
        ("RETRY_TASK", {"previous_failure": True}, "DENIED"),
        ("RETRY_TASK", {"repeated": True}, "DENIED"),
        ("RESTART_WORKER", {}, "APPROVAL_REQUIRED"),
        ("RESTART_WORKER", {"worker": "default@autopilot"}, "DENIED"),
        ("RESTART_WORKER", {"environment": "production"}, "DENIED"),
        ("PAUSE_QUEUE", {}, "APPROVAL_REQUIRED"),
        ("RESUME_QUEUE", {"queue": "unknown"}, "DENIED"),
        ("CLEAR_RETRY_STATE", {}, "APPROVAL_REQUIRED"),
        ("QUARANTINE_TASK", {}, "ALLOW"),
        ("QUARANTINE_TASK", {"task_status": "SUCCEEDED"}, "DENIED"),
        ("DROP_DATABASE", {}, "DENIED"),
        ("NO_ACTION", {"verified": False}, "NO_ACTION"),
        ("REQUEST_HUMAN", {}, "HUMAN_REVIEW"),
    ],
)
def test_policy_boundaries(action, changes, expected):
    assert evaluate(action, replace(BASE, **changes)).decision == expected


def test_dry_run_never_executes(db, incident, diagnosis):
    row = propose(db, incident, diagnosis)
    assert row.status == "DRY_RUN" and not row.requested
    assert execute_remediation(db, row) is None
    assert not db.scalar(select(RemediationExecution))
    assert propose(db, incident, diagnosis).id == row.id


def execute_mode(monkeypatch):
    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()


def test_one_execution_claim_and_preserved_retry_history(db, incident, diagnosis, task, monkeypatch):
    execute_mode(monkeypatch)
    task.retry_count = 4
    row = propose(db, incident, diagnosis)
    first = execute_remediation(db, row)
    assert first.status == "OBSERVING" and task.status == "PENDING" and task.retry_count == 4
    row.requested = True
    assert execute_remediation(db, row) is None
    assert len(list(db.scalars(select(RemediationExecution)))) == 1


def test_action_failure_is_uncertain_never_replayed(db, incident, diagnosis, monkeypatch):
    execute_mode(monkeypatch)
    monkeypatch.setattr(
        "app.remediation.service.dispatch_action", lambda *a: (_ for _ in ()).throw(TimeoutError())
    )
    row = propose(db, incident, diagnosis)
    execution = execute_remediation(db, row)
    assert execution.status == "UNCERTAIN"
    row.requested = True
    assert execute_remediation(db, row) is None


def test_execute_rechecks_mode(db, incident, diagnosis, monkeypatch):
    execute_mode(monkeypatch)
    row = propose(db, incident, diagnosis)
    monkeypatch.setenv("REMEDIATION_MODE", "dry_run")
    settings.cache_clear()
    assert execute_remediation(db, row) is None and row.status == "DENIED"


def test_approval_once_and_expiry(db, incident, diagnosis, monkeypatch):
    execute_mode(monkeypatch)
    diagnosis.recommended_action = "RESTART_WORKER"
    diagnosis.root_cause = "WORKER_FAILURE"
    row = propose(db, incident, diagnosis)
    db.flush()
    approval = db.scalar(select(ApprovalRequest))
    approval.expires_at = now() - timedelta(seconds=1)
    with pytest.raises(ValueError, match="expired"):
        decide_approval(db, row.id, True, "tester")
    approval.expires_at = now() + timedelta(minutes=2)
    decide_approval(db, row.id, False, "tester")
    with pytest.raises(ValueError, match="already"):
        decide_approval(db, row.id, True, "tester")
    assert not row.requested


@pytest.mark.parametrize(
    "action,status,online,healthy,queue,failures,expected",
    [
        ("RETRY_TASK", "SUCCEEDED", True, True, 0, 0, "RECOVERED"),
        ("QUARANTINE_TASK", "SUCCEEDED", True, True, 0, 0, "PARTIALLY_RECOVERED"),
        ("PAUSE_QUEUE", "SUCCEEDED", True, True, 0, 0, "PARTIALLY_RECOVERED"),
        ("RETRY_TASK", "SUCCEEDED", False, True, 0, 0, "FAILED"),
        ("RETRY_TASK", "SUCCEEDED", True, False, 0, 0, "FAILED"),
        ("RETRY_TASK", "SUCCEEDED", True, True, None, 0, "FAILED"),
        ("RETRY_TASK", "SUCCEEDED", True, True, 0, 1, "FAILED"),
        ("RETRY_TASK", "STARTED", True, True, 0, 0, "PARTIALLY_RECOVERED"),
        ("RESTART_WORKER", None, True, True, 0, 0, "RECOVERED"),
    ],
)
def test_recovery_requires_business_and_health_evidence(
    action, status, online, healthy, queue, failures, expected
):
    assert classify_recovery(action, status, online, healthy, queue, failures) == expected
