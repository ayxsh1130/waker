"""Evaluation-only fault orchestration. Never imported by diagnostic tools."""

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.schemas import TaskInput
from app.db.base import now, seconds, uid
from app.db.models import DeploymentRecord, FaultInjection, RuntimeSetting, TaskExecution
from app.investigation.verification import COMPONENT
from app.workers.publisher import create_task


def control(method, service, path, body=None):
    cfg = settings()
    url = {
        "dependency": cfg.dependency_url,
        "worker": cfg.worker_control_url,
        "proxy": cfg.proxy_control_url,
    }[service]
    with httpx.Client(timeout=8) as client:
        response = client.request(
            method, url + path, headers={"X-Control-Token": cfg.control_token}, json=body
        )
        response.raise_for_status()
        return response.json()


def schedule_fault(db, request, run_id=None):
    from app.db.locks import lock_experiment_resources
    from app.db.models import Experiment

    lock_experiment_resources(db)
    cfg = settings()
    if cfg.app_environment == "production" or not cfg.faults_enabled:
        raise ValueError("Fault injection is disabled in this environment")
    if run_id is None and db.scalar(
        select(Experiment.id).where(Experiment.status.in_(["QUEUED", "RUNNING"]))
    ):
        raise ValueError("An experiment owns the fault resources")
    if db.scalar(
        select(FaultInjection.id).where(FaultInjection.status.in_(["SCHEDULED", "ACTIVE", "RESETTING"]))
    ):
        raise ValueError("Another fault is active or awaiting cleanup")
    scope = uid()
    fault = FaultInjection(
        scope_id=scope,
        fault_type=request.fault_type.value,
        ground_truth=request.fault_type.value,
        component=COMPONENT.get(request.fault_type.value, "unknown"),
        parameters=request.model_dump(mode="json", exclude={"fault_type"}),
        run_id=run_id,
    )
    db.add(fault)
    db.add(RuntimeSetting(key="auto:" + scope, value=run_id is None))
    db.flush()
    return fault


def activate(db, fault):
    cause = fault.fault_type
    duration = fault.parameters["duration_seconds"]
    profile = {"ttl": min(duration, 120)}
    if cause == "API_TIMEOUT":
        profile["mode"] = "timeout"
    if cause == "DEPENDENCY_ERROR":
        profile["mode"] = "http500"
    if cause == "INTERMITTENT_FAILURE":
        profile["mode"] = "intermittent"
    if cause in {"TASK_EXCEPTION", "POISON_TASK"}:
        profile.update(exception=True, ttl=10 if cause == "TASK_EXCEPTION" else duration)
    if cause == "DATABASE_FAILURE":
        profile["database_unavailable"] = True
    if cause in {"OVERLOAD", "WORKER_FAILURE"}:
        profile["delay"] = 10
    if cause == "BAD_CONFIGURATION":
        profile["invalid_integer"] = True
    if cause == "DEPLOYMENT_REGRESSION":
        profile["revision"] = "v2"
        db.add(
            DeploymentRecord(
                scope_id=fault.scope_id,
                commit_hash="local-v2",
                version="v2",
                environment="test",
                changed_files=["workload_v2.py"],
                description="Local controlled workload release selected; inspect divisor change",
            )
        )
    control("PUT", "dependency", "/internal/profile/" + fault.scope_id, profile)
    if cause == "WORKER_FAILURE":
        control("POST", "worker", "/restart")
    if cause == "BROKER_DISRUPTION":
        control("POST", "proxy", "/proxies/broker", {"enabled": False})
    count = fault.parameters["task_count"]
    if cause == "OVERLOAD":
        count = min(30, max(count, settings().backlog_threshold + 5))
    name = (
        "call_external_api"
        if cause in {"API_TIMEOUT", "DEPENDENCY_ERROR", "INTERMITTENT_FAILURE", "HEALTHY"}
        else "data_processing_task"
    )
    for i in range(count):
        create_task(
            db,
            TaskInput(name=name, idempotency_key=fault.scope_id + ":" + str(i), count=10),
            fault.scope_id,
            "experiment",
        )
    fault.status = "ACTIVE"
    fault.injected_at = now()
    fault.parameters = {**fault.parameters, "activated_at": now().isoformat()}


def reset_fault(db, fault):
    fault.status = "RESETTING"
    db.commit()
    try:
        control("DELETE", "dependency", "/internal/profile/" + fault.scope_id)
        if fault.fault_type == "BROKER_DISRUPTION":
            control("POST", "proxy", "/proxies/broker", {"enabled": True})
        if fault.fault_type == "WORKER_FAILURE":
            control("POST", "worker", "/restart")
        fault.status = "RESET"
        fault.reset_at = now()
        fault.error = None
    except Exception as exc:
        fault.error = "Cleanup pending: " + type(exc).__name__


def tick_faults(db):
    for fault in list(
        db.scalars(
            select(FaultInjection).where(FaultInjection.status.in_(["SCHEDULED", "ACTIVE", "RESETTING"]))
        )
    ):
        if fault.status == "RESETTING":
            reset_fault(db, fault)
            continue
        if fault.status == "SCHEDULED":
            try:
                activate(db, fault)
            except Exception as exc:
                fault.error = "Activation failed: " + type(exc).__name__
                fault.status = "RESETTING"
            db.commit()
            continue
        if fault.fault_type == "WORKER_FAILURE" and not fault.parameters.get("crash_executed"):
            active = db.scalar(
                select(TaskExecution).where(
                    TaskExecution.scope_id == fault.scope_id, TaskExecution.status == "STARTED"
                )
            )
            if active:
                try:
                    control("POST", "worker", "/crash")
                    fault.parameters = {**fault.parameters, "crash_executed": True}
                    fault.injected_at = now()
                except httpx.HTTPError as exc:
                    fault.error = "Crash request failed: " + type(exc).__name__
        if fault.injected_at and seconds(now(), fault.injected_at) >= fault.parameters["duration_seconds"]:
            reset_fault(db, fault)
