import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as Obj

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.schemas import ExperimentInput, FaultInput
from app.db.base import now
from app.db.models import ExperimentRun, FaultInjection, RuntimeSetting, TaskExecution
from app.experiments.metrics import aggregate, evaluate_run
from app.experiments.service import create_experiment
from app.faults.service import activate, reset_fault, schedule_fault, tick_faults


@pytest.mark.parametrize(
    "cause",
    [
        "API_TIMEOUT",
        "DEPENDENCY_ERROR",
        "WORKER_FAILURE",
        "TASK_EXCEPTION",
        "POISON_TASK",
        "BROKER_DISRUPTION",
        "DATABASE_FAILURE",
        "OVERLOAD",
        "BAD_CONFIGURATION",
        "DEPLOYMENT_REGRESSION",
        "INTERMITTENT_FAILURE",
        "HEALTHY",
    ],
)
def test_fault_plan_creates_real_queued_work(db, monkeypatch, cause):
    calls = []
    monkeypatch.setattr("app.faults.service.control", lambda *a, **k: calls.append(a) or {})
    fault = schedule_fault(db, FaultInput(fault_type=cause))
    activate(db, fault)
    db.flush()
    tasks = list(db.scalars(select(TaskExecution).where(TaskExecution.scope_id == fault.scope_id)))
    assert tasks and all(t.queue == "experiment" and t.status == "PENDING" for t in tasks)
    assert calls[0][0] == "PUT" and calls[0][2].startswith("/internal/profile/")
    assert fault.status == "ACTIVE" and fault.injected_at


def test_failed_cleanup_remains_retryable(db, monkeypatch):
    fault = schedule_fault(db, FaultInput(fault_type="BROKER_DISRUPTION"))
    monkeypatch.setattr(
        "app.faults.service.control", lambda *a, **k: (_ for _ in ()).throw(ConnectionError())
    )
    reset_fault(db, fault)
    assert fault.status == "RESETTING"
    monkeypatch.setattr("app.faults.service.control", lambda *a, **k: {})
    tick_faults(db)
    assert fault.status == "RESET" and fault.reset_at


def test_only_one_fault_at_a_time(db):
    schedule_fault(db, FaultInput(fault_type="API_TIMEOUT"))
    with pytest.raises(ValueError, match="Another fault"):
        schedule_fault(db, FaultInput(fault_type="HEALTHY"))


def test_faults_are_disabled_in_production(db, monkeypatch):
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "a" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    settings.cache_clear()
    with pytest.raises(ValueError, match="disabled"):
        schedule_fault(db, FaultInput(fault_type="HEALTHY"))


def test_experiment_snapshot_and_automatic_isolation(db):
    experiment = create_experiment(
        db,
        ExperimentInput(
            name="Frozen experiment",
            configurations=["RULE_BASED", "FULL_SYSTEM"],
            faults=["API_TIMEOUT", "HEALTHY"],
            trials=2,
        ),
    )
    db.flush()
    runs = list(db.scalars(select(ExperimentRun)))
    assert len(runs) == 8 and experiment.history_snapshot == []
    assert runs[0].metadata_snapshot["temperature"] == 0
    fault = schedule_fault(db, FaultInput(fault_type="API_TIMEOUT"), runs[0].id)
    assert db.get(RuntimeSetting, "auto:" + fault.scope_id).value is False


def test_experiment_run_cap():
    with pytest.raises(ValueError):
        ExperimentInput(
            name="Too large",
            configurations=["RULE_BASED", "LLM_ONLY", "LLM_RAG", "AGENT_TOOLS", "AGENT_RAG", "FULL_SYSTEM"],
            faults=["API_TIMEOUT", "HEALTHY", "POISON_TASK"],
            trials=10,
        )


def test_metric_denominators_and_null_recovery():
    start = now()
    incident = Obj(created_at=start + timedelta(seconds=2), recovered_at=None)
    diagnosis = Obj(root_cause="API_TIMEOUT", recommended_action="RETRY_TASK", confidence=0.8)
    values = evaluate_run("API_TIMEOUT", start, incident=incident, diagnosis=diagnosis)
    healthy = evaluate_run("HEALTHY", start)
    assert (
        values["mttd_seconds"] == 2
        and values["mttr_seconds"] is None
        and healthy["diagnosis_accuracy"] is None
    )
    result = aggregate([values, healthy])
    assert result["metrics"]["diagnosis_accuracy"]["n"] == 1
    assert result["metrics"]["diagnosis_accuracy"]["total_runs"] == 2
    assert result["metrics"]["diagnosis_accuracy"]["missing_n"] == 1
    assert result["metrics"]["diagnosis_accuracy"]["coverage"] == 0.5
    assert result["metrics"]["mttr_seconds"]["n"] == 0 and result["detection"]["false_positive_rate"] == 0
    assert result["metrics"]["diagnosis_accuracy"]["sample_sd"] is None


def test_mttr_requires_confirmed_execution():
    start = now()
    incident = Obj(created_at=start + timedelta(seconds=2), recovered_at=start + timedelta(seconds=40))
    assert evaluate_run("API_TIMEOUT", start, incident)["mttr_seconds"] is None
    execution = Obj(status="RECOVERED", automatic=True)
    assert evaluate_run("API_TIMEOUT", start, incident, execution=execution)["mttr_seconds"] == 40


def test_healthy_false_positive_is_not_diagnosis_accuracy():
    incident = Obj(created_at=now(), recovered_at=None)
    values = evaluate_run("HEALTHY", now(), incident)
    assert values["false_positive"] == 1 and values["diagnosis_accuracy"] is None


def test_test_dependency_serves_actual_fault_responses():
    path = Path(__file__).parents[2] / "services/test-dependency/main.py"
    if not path.exists():
        path = Path("/services/test-dependency/main.py")
    spec = importlib.util.spec_from_file_location("test_dependency_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.put("/internal/profile/test", json={"mode": "http500"}).status_code == 403
        headers = {"X-Control-Token": "test-control-credential"}
        assert (
            client.put("/internal/profile/test", json={"mode": "intermittent"}, headers=headers).status_code
            == 200
        )
        assert [client.get("/work/test").status_code for _ in range(4)] == [500, 200, 500, 200]
        assert len(client.get("/observations/test").json()["requests"]) == 4
        client.delete("/internal/profile/test", headers=headers)
        assert client.get("/work/test").status_code == 200


def test_restart_recovery_never_replays_action(db, incident, diagnosis):
    from app.control import recover_interrupted
    from app.db.models import RemediationExecution
    from app.remediation.service import propose

    remediation = propose(db, incident, diagnosis)
    execution = RemediationExecution(remediation_id=remediation.id, status="EXECUTING", automatic=True)
    db.add(execution)
    db.flush()
    recover_interrupted(db)
    assert execution.status == "UNCERTAIN" and incident.status == "HUMAN_REVIEW"
    assert not remediation.requested


def test_experiment_waits_for_worker_before_injection(db, monkeypatch):
    import threading

    from app.experiments.runner import run_next

    experiment = create_experiment(
        db, ExperimentInput(name="Readiness gate", configurations=["RULE_BASED"], faults=["HEALTHY"])
    )
    db.commit()
    monkeypatch.setattr("app.experiments.runner.redis_status", lambda *a: {"status": "HEALTHY"})
    run_next(threading.Event())
    db.expire_all()
    run = db.scalar(select(ExperimentRun).where(ExperimentRun.experiment_id == experiment.id))
    assert run.status == "QUEUED" and "Waiting for a healthy" in run.error
    assert not db.scalar(select(FaultInjection))
