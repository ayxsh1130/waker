from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.auth import COOKIE
from app.core.config import settings
from app.core.schemas import TaskInput
from app.db.models import TaskExecution, TaskLog
from app.workers.publisher import create_task, dispatch_pending
from app.workers.tasks import execute


def test_api_task_acceptance_idempotency_and_payload_conflict(client):
    payload = {"name": "data_processing_task", "idempotency_key": "same-key-001", "count": 5}
    first = client.post("/api/tasks", json=payload)
    second = client.post("/api/tasks", json=payload)
    assert first.status_code == 202 and first.json()["id"] == second.json()["id"]
    assert client.post("/api/tasks", json={**payload, "count": 6}).status_code == 422
    detail = client.get("/api/tasks/" + first.json()["id"]).json()
    assert (
        detail["task"]["status"] == "PENDING"
        and detail["logs"][0]["message"] == "Task accepted for durable dispatch"
    )


def test_api_input_limits_and_origin_protection(client):
    assert client.post("/api/workload", json={"count": 101}).status_code == 422
    assert client.post("/api/faults/inject", json={"fault_type": "UNKNOWN"}).status_code == 422
    assert (
        client.post(
            "/api/workload", json={"count": 1}, headers={"Origin": "https://foreign.example"}
        ).status_code
        == 403
    )
    assert client.get("/health").status_code == 200
    assert client.get("/api/tasks/missing").status_code == 404


def test_auth_cookie_and_secret_non_disclosure(client, monkeypatch, db):
    from app.identity.service import create_account

    create_account(db, "admin@example.test", "a long unique password", "admin", bootstrap=True)
    db.commit()
    token = "a-secure-test-admin-token-at-least-32-chars"
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_TOKEN", token)
    monkeypatch.setenv("GROQ_API_KEY", "never-send-to-browser")
    settings.cache_clear()
    assert client.get("/api/tasks").status_code == 401
    assert (
        client.post("/api/session", json={"email": "admin@example.test", "password": "wrong"}).status_code
        == 401
    )
    response = client.post(
        "/api/session", json={"email": "admin@example.test", "password": "a long unique password"}
    )
    assert response.status_code == 200 and "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    config = client.get("/api/settings")
    assert config.status_code == 200
    assert token not in config.text and "never-send-to-browser" not in config.text
    client.cookies.set(COOKIE, "forged", domain="testserver.local", path="/")
    assert client.get("/api/tasks").status_code == 401


def test_retired_shared_token_login_cannot_authenticate(client, monkeypatch):
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 40)
    settings.cache_clear()
    response = client.post(
        "/api/session/login",
        content="token=" + "x" * 40,
        headers={"content-type": "application/x-www-form-urlencoded", "origin": "http://localhost:5173"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert client.post("/api/session", json={"token": "x" * 40}).status_code == 422


def test_idempotent_execution_and_durable_log(db, task, monkeypatch):
    calls = []
    monkeypatch.setattr("app.workers.tasks.run_operation", lambda *a: calls.append(a) or {"result": 42})
    db.commit()
    execute.push_request(hostname="default@autopilot")
    try:
        assert execute.run(task.id) == {"result": 42}
        assert execute.run(task.id)["status"] == "DUPLICATE_OR_QUARANTINED"
    finally:
        execute.pop_request()
    db.expire_all()
    assert db.get(TaskExecution, task.id).status == "SUCCEEDED"
    assert len(calls) == 1 and db.scalar(select(TaskLog).where(TaskLog.message == "Execution succeeded"))


def test_non_idempotent_redelivery_is_uncertain(db, monkeypatch):
    task = create_task(db, TaskInput(name="send_email", idempotency_key="email-once-001"))
    task.status = "STARTED"
    db.commit()
    monkeypatch.setattr(
        "app.workers.tasks.run_operation", lambda *a: pytest.fail("Email must not be sent again")
    )
    assert execute.run(task.id)["status"] == "UNCERTAIN"
    db.expire_all()
    assert task.status == "UNCERTAIN"


def test_retry_exhaustion_quarantines_poison(db, task, monkeypatch):
    task.retry_count = 3
    db.commit()
    monkeypatch.setattr(
        "app.workers.tasks.run_operation", lambda *a: (_ for _ in ()).throw(ValueError("bad record"))
    )
    with pytest.raises(ValueError):
        execute.run(task.id)
    db.expire_all()
    assert task.retry_count == 4 and task.quarantined and task.status == "FAILED"
    assert execute.run(task.id)["status"] == "DUPLICATE_OR_QUARANTINED"


def test_delivery_limit_is_bounded(db, task):
    task.delivery_count = 5
    db.commit()
    assert execute.run(task.id)["status"] == "QUARANTINED"


def test_outbox_keeps_pending_task_after_broker_error(db, task, monkeypatch):
    monkeypatch.setattr(
        "app.workers.celery_app.celery_app.send_task",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError()),
    )
    dispatch_pending(db)
    assert task.status == "PENDING" and task.dispatch_attempts == 1
    assert task.dispatch_after > task.created_at


@pytest.mark.parametrize(
    "name,extension",
    [
        ("process_report", ".csv"),
        ("resize_image", ".png"),
        ("process_database_record", None),
        ("data_processing_task", None),
    ],
)
def test_real_operations_produce_artifacts_or_records(name, extension, tmp_path, db, monkeypatch):
    import httpx

    from app.workers import operations

    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    monkeypatch.setattr(operations.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    task = create_task(db, TaskInput(name=name, idempotency_key="operation-" + name, count=5, size=64))
    db.commit()
    result = operations.run_operation(name, task.payload, task.id, "local", task.correlation_id)
    if extension:
        artifact = Path(settings().artifact_dir) / result["artifact"]
        assert artifact.suffix == extension and artifact.stat().st_size > 20
        if extension == ".png":
            from PIL import Image

            assert Image.open(artifact).size == (64, 64)
        else:
            assert "record,square" in artifact.read_text()
    elif name == "process_database_record":
        again = operations.run_operation(name, task.payload, task.id, "local", task.correlation_id)
        assert again == result == {"sum": 15, "count": 5}
    else:
        assert result["mean"] == 3


def test_live_websocket_and_persisted_notification(client, db):
    from app.observability.events import emit

    with client.websocket_connect("/api/ws", headers={"origin": "http://localhost:5173"}) as ws:
        assert ws.receive_json()["type"] == "events"
        emit(db, "test", "record", {"status": "observed"})
        db.commit()
        event = ws.receive_json()
        assert event["events"][0]["data"]["status"] == "observed"


def test_transaction_failure_does_not_return_task_accepted(client, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    def fail_commit(self):
        raise IntegrityError("commit", {}, Exception("conflict"))

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = client.post(
        "/api/tasks", json={"name": "data_processing_task", "idempotency_key": "commit-failure-key"}
    )
    assert response.status_code == 409
