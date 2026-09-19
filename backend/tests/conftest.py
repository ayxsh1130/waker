import os

os.environ["APP_ENVIRONMENT"] = "test"
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
os.environ["LLM_PROVIDER"] = "none"
os.environ["INTERNAL_TOKEN"] = "test-control-credential"
import pytest

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine, session_scope


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AUTO_INVESTIGATE", "false")
    monkeypatch.setenv("LOCAL_REPOSITORY_PATH", str(tmp_path / "source"))
    monkeypatch.setenv("REMEDIATION_MODE", "dry_run")
    monkeypatch.setenv("APP_AUTH_ENABLED", "false")
    (tmp_path / "source").mkdir()
    settings.cache_clear()
    engine.cache_clear()
    Base.metadata.create_all(engine())
    yield
    engine().dispose()
    engine.cache_clear()
    settings.cache_clear()


@pytest.fixture
def db():
    with session_scope() as db:
        yield db


@pytest.fixture
def task(db):
    from app.core.schemas import TaskInput
    from app.workers.publisher import create_task

    return create_task(db, TaskInput(name="call_external_api", idempotency_key="test-task-0001", count=4))


@pytest.fixture
def incident(db, task):
    from app.incidents.detector import record_signal

    task.status = "FAILED"
    task.exception = "ReadTimeout: dependency request exceeded timeout"
    task.worker = "experiment@autopilot"
    return record_signal(db, "dependency", "task_failure", {"exception": task.exception}, task)


@pytest.fixture
def diagnosis(db, incident):
    from app.agents.workflow import create_investigation
    from app.db.models import Diagnosis, Verification

    inv = create_investigation(db, incident, "FULL_SYSTEM")
    d = Diagnosis(
        investigation_id=inv.id,
        root_cause="API_TIMEOUT",
        summary="The dependency request exceeded its timeout.",
        affected_component="dependency",
        confidence=0.9,
        supporting_evidence=[],
        contradicting_evidence=[],
        alternative_hypotheses=[],
        recommended_action="RETRY_TASK",
    )
    db.add(d)
    db.flush()
    db.add(
        Verification(
            diagnosis_id=d.id,
            verified=True,
            verification_score=1,
            supporting_checks=["test fixture"],
            contradictions=[],
            missing_evidence=[],
        )
    )
    db.flush()
    return d


@pytest.fixture
def probes(monkeypatch):
    from app.tools import probes

    monkeypatch.setattr(probes, "redis_status", lambda *a, **k: {"status": "HEALTHY"})
    monkeypatch.setattr(
        probes, "queue_metrics", lambda: {"status": "HEALTHY", "depths": {"default": 0, "experiment": 0}}
    )
    monkeypatch.setattr(probes, "database_status", lambda: {"status": "HEALTHY"})
    monkeypatch.setattr(
        probes,
        "external_status",
        lambda scope: {"status": "DEGRADED", "probes": [{"error_type": "ReadTimeout"}], "observations": []},
    )
    monkeypatch.setattr(probes, "http_health", lambda *a, **k: {"status": "HEALTHY"})


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client
