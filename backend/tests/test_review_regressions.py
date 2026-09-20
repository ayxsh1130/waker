from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.schemas import Cause, DiagnosisOutput
from app.db.base import now
from app.db.models import Verification
from app.investigation.verification import verify_diagnosis
from app.remediation.recovery import classify_recovery
from app.remediation.service import propose


def worker_evidence(status="OFFLINE", age=0, broker=None):
    stamp = (now() - timedelta(seconds=age)).isoformat()
    return [
        {
            "id": "alert",
            "kind": "initial_signal",
            "available": True,
            "timestamp": (now() - timedelta(minutes=10)).isoformat(),
            "content": {"signal": "heartbeat_lost", "worker": "experiment@autopilot", "status": "OFFLINE"},
        },
        {
            "id": "worker",
            "kind": "get_worker_status",
            "available": True,
            "timestamp": stamp,
            "content": {
                "target_worker": "experiment@autopilot",
                "workers": [
                    {
                        "name": "experiment@autopilot",
                        "status": status,
                        "heartbeat_age_seconds": 1 if status == "ONLINE" else 60,
                    }
                ],
            },
        },
        {
            "id": "broker",
            "kind": "get_redis_status",
            "available": True,
            "timestamp": stamp,
            "content": {"broker": broker or {"status": "HEALTHY"}},
        },
    ]


def worker_diagnosis(healthy=False):
    return DiagnosisOutput(
        root_cause="HEALTHY" if healthy else "WORKER_FAILURE",
        summary="Check the incident worker against current broker and heartbeat observations.",
        affected_component="worker",
        confidence=0.9,
        supporting_evidence=["alert", "worker", "broker"],
        contradicting_evidence=[],
        alternative_hypotheses=[],
        recommended_action="NO_ACTION" if healthy else "RESTART_WORKER",
    )


def test_worker_verification_does_not_depend_on_json_key_order():
    evidence = worker_evidence(broker={"latency_ms": 1, "connected_clients": 4, "status": "HEALTHY"})
    assert verify_diagnosis(worker_diagnosis(), evidence).verified


def test_old_alert_cannot_override_current_online_worker():
    assert not verify_diagnosis(worker_diagnosis(), worker_evidence("ONLINE")).verified


def test_worker_recovery_rejects_stale_observations():
    assert not verify_diagnosis(worker_diagnosis(True), worker_evidence("ONLINE", age=300)).verified


def test_denied_proposal_can_be_reevaluated_after_verification(db, incident, diagnosis, monkeypatch):
    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()
    verification = db.scalar(select(Verification).where(Verification.diagnosis_id == diagnosis.id))
    verification.verified = False
    row = propose(db, incident, diagnosis)
    assert row.policy_decision == "DENIED"
    verification.verified = True
    again = propose(db, incident, diagnosis)
    assert again.id == row.id
    assert again.policy_decision == "ALLOW" and again.requested


def test_worker_only_incident_can_recover_without_task():
    assert classify_recovery("RESTART_WORKER", None, True, True, 0, 0) == "RECOVERED"


@pytest.mark.parametrize("age", [121, 300, -30])
def test_worker_failure_rejects_old_or_future_observations(age):
    assert not verify_diagnosis(worker_diagnosis(), worker_evidence(age=age)).verified


def test_worker_failure_rejects_different_worker():
    rows = worker_evidence()
    rows[1]["content"]["target_worker"] = "default@autopilot"
    rows[1]["content"]["workers"][0]["name"] = "default@autopilot"
    assert not verify_diagnosis(worker_diagnosis(), rows).verified


def test_new_unavailable_probe_cannot_be_hidden_by_older_healthy_probe():
    rows = worker_evidence(age=20)
    rows.append({**rows[2], "id": "new-broker", "timestamp": now().isoformat(), "available": False})
    assert not verify_diagnosis(worker_diagnosis(), rows).verified


def test_worker_failure_requires_cited_broker():
    diagnosis = worker_diagnosis().model_copy(update={"supporting_evidence": ["alert", "worker"]})
    assert not verify_diagnosis(diagnosis, worker_evidence()).verified


def test_broker_rule_is_order_independent():
    from app.agents.rules import diagnose_rule

    rows = worker_evidence(broker={"error_type": "ConnectionError", "status": "UNAVAILABLE"})
    assert diagnose_rule(rows).root_cause.value == "BROKER_DISRUPTION"
    diagnosis = worker_diagnosis().model_copy(
        update={
            "root_cause": Cause.BROKER_DISRUPTION,
            "affected_component": "broker",
        }
    )
    assert verify_diagnosis(diagnosis, rows).verified


def approval_setup(db, incident, diagnosis, monkeypatch):
    from app.db.models import ApprovalRequest

    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()
    diagnosis.root_cause = "WORKER_FAILURE"
    diagnosis.affected_component = "worker"
    diagnosis.recommended_action = "RESTART_WORKER"
    incident.component = "worker"
    incident.queue = "experiment"
    row = propose(db, incident, diagnosis)
    db.flush()
    return row, db.scalar(select(ApprovalRequest).where(ApprovalRequest.remediation_id == row.id))


def test_execute_waits_for_approval(db, incident, diagnosis, monkeypatch):
    from app.remediation.service import execute_remediation

    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    monkeypatch.setattr("app.remediation.service.dispatch_action", lambda *a: pytest.fail("No approval"))
    assert approval.status == "PENDING"
    assert execute_remediation(db, row) is None


@pytest.mark.parametrize(
    "worker_status,broker_status,executed",
    [
        ("OFFLINE", "HEALTHY", True),
        ("ONLINE", "HEALTHY", False),
        ("OFFLINE", "UNAVAILABLE", False),
    ],
)
def test_approved_restart_rechecks_live_conditions(
    db,
    incident,
    diagnosis,
    monkeypatch,
    worker_status,
    broker_status,
    executed,
):
    from app.db.models import Worker
    from app.remediation.service import decide_approval, execute_remediation

    row, _ = approval_setup(db, incident, diagnosis, monkeypatch)
    db.add(
        Worker(
            name=incident.worker,
            status=worker_status,
            last_heartbeat=now() - timedelta(seconds=60 if worker_status == "OFFLINE" else 1),
        )
    )
    db.flush()
    monkeypatch.setattr("app.tools.probes.redis_status", lambda *a: {"status": broker_status})
    calls = []
    monkeypatch.setattr("app.remediation.service.dispatch_action", lambda *a: calls.append(1) or {})
    decide_approval(db, row.id, True, "tester")
    result = execute_remediation(db, row)
    assert bool(calls) == executed
    assert (result is not None) == executed
    if not executed:
        assert row.status == "DENIED"


def test_denial_does_not_poison_new_diagnosis_cooldown(db, incident, diagnosis, monkeypatch):
    from app.db.models import Diagnosis

    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()
    verification = db.scalar(select(Verification).where(Verification.diagnosis_id == diagnosis.id))
    verification.verified = False
    assert propose(db, incident, diagnosis).status == "DENIED"
    new = Diagnosis(
        **{key: getattr(diagnosis, key) for key in DiagnosisOutput.model_fields},
        investigation_id=diagnosis.investigation_id,
    )
    db.add(new)
    db.flush()
    db.add(
        Verification(
            diagnosis_id=new.id,
            verified=True,
            verification_score=1,
            supporting_checks=[],
            contradictions=[],
            missing_evidence=[],
        )
    )
    db.flush()
    assert propose(db, incident, new).policy_decision == "ALLOW"


def test_expired_approval_is_removed_from_pending(db, incident, diagnosis, monkeypatch):
    from app.remediation.service import expire_approvals

    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    approval.expires_at = now() - timedelta(seconds=1)
    db.flush()
    expire_approvals(db)
    assert approval.status == row.status == "EXPIRED"
    assert not row.requested


def test_rejected_approval_is_not_reopened(db, incident, diagnosis, monkeypatch):
    from app.remediation.service import decide_approval

    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    decide_approval(db, row.id, False, "tester")
    assert propose(db, incident, diagnosis).status == "REJECTED"
    assert approval.status == "REJECTED"


def test_action_must_match_verified_cause(db, incident, diagnosis, monkeypatch):
    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()
    diagnosis.recommended_action = "RESTART_WORKER"
    assert propose(db, incident, diagnosis).policy_decision == "DENIED"


def make_fault(db, incident, age=650):
    from app.db.models import FaultInjection

    fault = FaultInjection(
        scope_id=incident.scope_id,
        fault_type="WORKER_FAILURE",
        ground_truth="WORKER_FAILURE",
        component="worker",
        status="ACTIVE",
        injected_at=now() - timedelta(seconds=age),
        parameters={"duration_seconds": 600, "task_count": 1, "crash_executed": True},
    )
    db.add(fault)
    db.flush()
    return fault


def test_fault_cleanup_waits_for_pending_approval(db, incident, diagnosis, monkeypatch):
    from app.faults.service import tick_faults

    _, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    fault = make_fault(db, incident)
    monkeypatch.setattr("app.faults.service.control", lambda *a: pytest.fail("Approval still pending"))
    tick_faults(db)
    assert fault.status == "ACTIVE" and approval.status == "PENDING"


def test_cleanup_has_hard_limit_and_cancels_approval(db, incident, diagnosis, monkeypatch):
    from app.faults.service import tick_faults

    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    fault = make_fault(db, incident, age=1801)
    calls = []
    monkeypatch.setattr("app.faults.service.control", lambda *a: calls.append(a) or {})
    tick_faults(db)
    assert fault.status == "RESET"
    assert row.status == approval.status == "CANCELLED" and not row.requested
    assert any(call[2] == "/restart" for call in calls)


def test_manual_reset_cancels_restart_approval(db, incident, diagnosis, monkeypatch):
    from app.faults.service import reset_fault

    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    fault = make_fault(db, incident, age=10)
    monkeypatch.setattr("app.faults.service.control", lambda *a: {})
    reset_fault(db, fault)
    assert row.status == approval.status == "CANCELLED"


def test_refresh_verification_records_new_evidence_without_editing_diagnosis(
    db,
    incident,
    diagnosis,
    monkeypatch,
):
    from app.db.models import Evidence, Worker
    from app.investigation.service import verify_current

    diagnosis.root_cause = "WORKER_FAILURE"
    diagnosis.affected_component = "worker"
    diagnosis.recommended_action = "RESTART_WORKER"
    db.add(Worker(name=incident.worker, status="OFFLINE", last_heartbeat=now() - timedelta(seconds=90)))
    db.flush()
    monkeypatch.setattr("app.tools.probes.redis_status", lambda *a: {"status": "HEALTHY"})
    result = verify_current(db, incident, diagnosis)
    assert result.verified
    assert diagnosis.supporting_evidence == []
    assert (
        len(list(db.scalars(select(Evidence).where(Evidence.retrieval_method == "verification_check")))) == 2
    )
    from app.db.models import IncidentEvent

    event = db.scalar(
        select(IncidentEvent)
        .where(IncidentEvent.kind == "Verification")
        .order_by(IncidentEvent.created_at.desc())
    )
    assert event.content["verification_id"] == result.id
    assert len(event.content["verifier_evidence_ids"]) == 2


def test_refresh_does_not_erase_recovered_state(db, incident, diagnosis, probes):
    from app.investigation.service import verify_current

    incident.status = "RECOVERED"
    verify_current(db, incident, diagnosis)
    assert incident.status == "RECOVERED"


def test_worker_failure_default_duration():
    from app.core.schemas import FaultInput

    assert FaultInput(fault_type="WORKER_FAILURE").duration_seconds == 600
    assert FaultInput(fault_type="API_TIMEOUT").duration_seconds == 40
    with pytest.raises(ValueError):
        FaultInput(fault_type="API_TIMEOUT", duration_seconds=600)


def test_fresh_unavailable_dependency_check_invalidates_old_success():
    rows = [
        {"id": "task", "kind": "get_task_details", "content": {"exception": "ReadTimeout"}},
        {
            "id": "old",
            "kind": "get_external_service_status",
            "available": True,
            "timestamp": (now() - timedelta(seconds=30)).isoformat(),
            "content": {"status": "HEALTHY"},
        },
        {
            "id": "new",
            "kind": "get_external_service_status",
            "available": False,
            "timestamp": now().isoformat(),
            "content": {"error_type": "ConnectionError"},
        },
    ]
    output = DiagnosisOutput(
        root_cause="API_TIMEOUT",
        affected_component="dependency",
        summary="The request timed out during task execution.",
        confidence=0.9,
        supporting_evidence=["task", "old", "new"],
        contradicting_evidence=[],
        alternative_hypotheses=[],
        recommended_action="RETRY_TASK",
    )
    assert not verify_diagnosis(output, rows).verified


def test_http_error_is_not_inferred_from_latency_number():
    rows = [
        {"id": "task", "kind": "get_task_details", "content": {"exception": ""}},
        {
            "id": "probe",
            "kind": "get_external_service_status",
            "available": True,
            "timestamp": now().isoformat(),
            "content": {"status": "HEALTHY", "probes": [{"status_code": 200, "duration_seconds": 0.500}]},
        },
    ]
    output = DiagnosisOutput(
        root_cause="DEPENDENCY_ERROR",
        affected_component="dependency",
        summary="The dependency was tested for HTTP errors.",
        confidence=0.9,
        supporting_evidence=["task", "probe"],
        contradicting_evidence=[],
        alternative_hypotheses=[],
        recommended_action="RETRY_TASK",
    )
    assert not verify_diagnosis(output, rows).verified


def test_dry_run_can_be_explicitly_reevaluated_in_execute_mode(db, incident, diagnosis, monkeypatch):
    row = propose(db, incident, diagnosis)
    assert row.status == "DRY_RUN"
    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()
    assert propose(db, incident, diagnosis).requested


def test_old_proposal_recent_execution_still_has_cooldown(db, incident, diagnosis, monkeypatch):
    from app.db.models import RemediationExecution
    from app.remediation.service import context_for

    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()
    row = propose(db, incident, diagnosis)
    row.created_at = now() - timedelta(minutes=10)
    db.add(RemediationExecution(remediation_id=row.id, status="RECOVERED", automatic=True))
    db.flush()
    assert context_for(db, incident, diagnosis)[0].repeated


def test_approval_expiring_during_preflight_cannot_dispatch(db, incident, diagnosis, monkeypatch):
    from app.remediation.service import decide_approval, execute_remediation

    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    decide_approval(db, row.id, True, "tester")

    def preflight(*args):
        approval.expires_at = now() - timedelta(seconds=1)
        return True, []

    monkeypatch.setattr("app.remediation.service.restart_preflight", preflight)
    monkeypatch.setattr("app.remediation.service.dispatch_action", lambda *a: pytest.fail("Expired"))
    assert execute_remediation(db, row) is None
    assert row.status == approval.status == "EXPIRED"


def test_stale_approval_is_not_marked_approved_on_validation_error(db, incident, diagnosis, monkeypatch):
    from app.remediation.service import decide_approval

    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    monkeypatch.setenv("REMEDIATION_MODE", "dry_run")
    settings.cache_clear()
    with pytest.raises(ValueError, match="no longer executable"):
        decide_approval(db, row.id, True, "tester")
    assert approval.status == "PENDING" and approval.decided_at is None


def test_fault_without_approval_still_cleans_up(db, incident, monkeypatch):
    from app.faults.service import tick_faults

    fault = make_fault(db, incident)
    calls = []
    monkeypatch.setattr("app.faults.service.control", lambda *a: calls.append(a) or {})
    tick_faults(db)
    assert fault.status == "RESET" and calls


def make_execution(db, incident, diagnosis):
    from app.db.models import RemediationExecution

    diagnosis.root_cause = "WORKER_FAILURE"
    diagnosis.affected_component = "worker"
    diagnosis.recommended_action = "RESTART_WORKER"
    incident.component = "worker"
    row = propose(db, incident, diagnosis)
    # Observation tests start after an already-dispatched restart.
    row.status = "OBSERVING"
    execution = RemediationExecution(
        remediation_id=row.id,
        status="OBSERVING",
        automatic=False,
        observe_after=now() - timedelta(seconds=1),
        created_at=now() - timedelta(seconds=25),
    )
    db.add(execution)
    db.flush()
    return execution


def test_worker_only_recovery_waits_without_crashing(db, incident, diagnosis, probes):
    from app.remediation.recovery import observe_recoveries

    incident.task_id = None
    execution = make_execution(db, incident, diagnosis)
    observe_recoveries(db)
    assert execution.status == "OBSERVING"
    assert execution.post_result["task_status"] is None


def test_worker_only_recovery_succeeds_with_fresh_heartbeat(db, incident, diagnosis, probes):
    from app.db.models import Worker
    from app.remediation.recovery import observe_recoveries

    incident.task_id = None
    incident.queue = None
    db.add(Worker(name=incident.worker, status="ONLINE", last_heartbeat=now()))
    execution = make_execution(db, incident, diagnosis)
    observe_recoveries(db)
    assert execution.status == "RECOVERED"


def test_stale_online_worker_cannot_prove_recovery(db, incident, diagnosis, probes, task):
    from app.db.models import Worker
    from app.remediation.recovery import observe_recoveries

    task.status = "SUCCEEDED"
    db.add(Worker(name=incident.worker, status="ONLINE", last_heartbeat=now() - timedelta(minutes=5)))
    execution = make_execution(db, incident, diagnosis)
    execution.created_at = now() - timedelta(minutes=5)
    db.flush()
    observe_recoveries(db)
    assert execution.status == "FAILED" and not execution.post_result["worker_healthy"]


def test_cleanup_cannot_be_claimed_as_approved_recovery(db, incident, diagnosis, probes, task):
    from app.db.models import Worker
    from app.remediation.recovery import observe_recoveries

    db.add(Worker(name=incident.worker, status="ONLINE", last_heartbeat=now()))
    task.status = "SUCCEEDED"
    fault = make_fault(db, incident)
    fault.status = "RESET"
    fault.reset_at = now()
    execution = make_execution(db, incident, diagnosis)
    observe_recoveries(db)
    assert execution.status == "UNCERTAIN"
    assert execution.post_result["cleanup_intervened"] is True


def test_full_worker_approval_lifecycle(db, incident, diagnosis, monkeypatch, probes):
    from app.db.models import Worker
    from app.faults.service import tick_faults
    from app.investigation.service import verify_current
    from app.remediation.recovery import observe_recoveries
    from app.remediation.service import decide_approval, execute_remediation

    incident.task_id = None
    diagnosis.root_cause = "WORKER_FAILURE"
    diagnosis.affected_component = "worker"
    diagnosis.recommended_action = "RESTART_WORKER"
    worker = Worker(name=incident.worker, status="OFFLINE", last_heartbeat=now() - timedelta(seconds=90))
    db.add(worker)
    db.flush()
    assert verify_current(db, incident, diagnosis).verified
    row, approval = approval_setup(db, incident, diagnosis, monkeypatch)
    fault = make_fault(db, incident)
    monkeypatch.setattr("app.faults.service.control", lambda *a: pytest.fail("Must wait for approval"))
    tick_faults(db)
    assert fault.status == "ACTIVE" and approval.status == "PENDING"
    assert execute_remediation(db, row) is None
    calls = []
    monkeypatch.setattr("app.remediation.service.dispatch_action", lambda *a: calls.append(1) or {})
    decide_approval(db, row.id, True, "tester")
    execution = execute_remediation(db, row)
    assert calls == [1] and not execution.automatic
    worker.status, worker.last_heartbeat = "ONLINE", now()
    execution.observe_after = now() - timedelta(seconds=1)
    db.flush()
    observe_recoveries(db)
    assert execution.status == incident.status == "RECOVERED"
    assert execute_remediation(db, row) is None


def test_optional_llm_and_github_do_not_break_operational_health(probes, monkeypatch):
    from app.api.queries import health

    monkeypatch.setenv("EMBEDDING_PROVIDER", "disabled")
    settings.cache_clear()
    result = health()
    assert result["services"]["llm"]["configured"] is False
    assert result["status"] == "HEALTHY"


def test_latest_failed_verification_excludes_incident_from_memory(db, incident, diagnosis):
    from app.db.models import HistoricalIncident
    from app.retrieval.client import remember_recovered

    incident.status = "RECOVERED"
    db.add(
        Verification(
            diagnosis_id=diagnosis.id,
            verified=False,
            verification_score=0,
            supporting_checks=[],
            contradictions=[],
            missing_evidence=["Current checks"],
        )
    )
    db.flush()
    remember_recovered(db, incident, diagnosis)
    assert not db.scalar(select(HistoricalIncident))


def test_task_retry_clears_completion_time_but_keeps_retry_history(db, task):
    from types import SimpleNamespace

    from app.remediation.service import dispatch_action

    task.completed_at, task.retry_count = now(), 4
    dispatch_action(db, SimpleNamespace(action="RETRY_TASK", parameters={"task_id": task.id}))
    assert task.status == "PENDING" and task.completed_at is None and task.retry_count == 4


def test_successful_task_clears_current_error_but_preserves_logs(db, task, monkeypatch):
    from app.workers.tasks import execute

    task.exception, task.stack_trace = "Previous failure", "Historical trace"
    db.commit()
    monkeypatch.setattr("app.workers.tasks.run_operation", lambda *a: {"done": True})
    execute.run(task.id)
    db.refresh(task)
    assert task.status == "SUCCEEDED" and task.exception is None and task.stack_trace is None
