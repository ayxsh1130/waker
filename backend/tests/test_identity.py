from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.core.auth import COOKIE
from app.core.config import settings
from app.db.base import now
from app.db.models import (
    AccessAudit,
    Account,
    Application,
    ApprovalRequest,
    ConnectorCredential,
    LoginBucket,
    LoginSession,
)
from app.identity.service import LOCAL_APPLICATION, create_account, digest

PASSWORD = "a unique long test password"
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture
def accounts(db, monkeypatch):
    admin = create_account(db, "admin@example.test", PASSWORD, "admin", bootstrap=True)
    viewer = create_account(db, "viewer@example.test", PASSWORD, "viewer", [LOCAL_APPLICATION])
    operator = create_account(db, "operator@example.test", PASSWORD, "operator", [LOCAL_APPLICATION])
    remote = Application(name="Remote application")
    other = Application(name="Another application")
    db.add_all([remote, other])
    db.flush()
    outsider = create_account(db, "remote@example.test", PASSWORD, "operator", [remote.id])
    db.commit()
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    settings.cache_clear()
    return {
        "admin": admin,
        "viewer": viewer,
        "operator": operator,
        "outsider": outsider,
        "remote": remote,
        "other": other,
    }


def sign_in(client, account):
    response = client.post(
        "/api/session", json={"email": account.email, "password": PASSWORD}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client.cookies.get(COOKIE)


def test_no_public_bootstrap_or_legacy_admin_token(client, monkeypatch):
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 40)
    settings.cache_clear()
    assert not client.get("/api/session").json()["authenticated"]
    assert (
        client.post(
            "/api/accounts", json={"email": "a@example.test", "password": PASSWORD, "role": "admin"}
        ).status_code
        == 401
    )
    assert client.post("/api/session", json={"token": "x" * 40}).status_code == 422
    assert client.get("/api/tasks", headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401


def test_bootstrap_once_and_password_hashing(db, accounts):
    account = accounts["admin"]
    assert account.password_hash.startswith("$argon2id$")
    assert PASSWORD not in account.password_hash
    with pytest.raises(ValueError, match="Accounts already exist"):
        create_account(db, "another@example.test", PASSWORD, "admin", bootstrap=True)


def test_cookie_only_hashed_in_database_logout_revokes_replay(client, db, accounts):
    token = sign_in(client, accounts["admin"])
    record = db.scalar(select(LoginSession))
    assert record.token_hash == digest(token) and record.token_hash != token
    assert client.get("/api/tasks").status_code == 200
    assert client.delete("/api/session").status_code == 200
    client.cookies.set(COOKIE, token)
    assert client.get("/api/tasks").status_code == 401
    db.expire_all()
    assert record.revoked_at


def test_login_rotation_invalidates_previous_cookie(client, accounts):
    first = sign_in(client, accounts["admin"])
    second = sign_in(client, accounts["admin"])
    assert first != second
    client.cookies.clear()
    client.cookies.set(COOKIE, first)
    assert client.get("/api/tasks").status_code == 401


def test_expired_cookie_rejected(client, db, accounts):
    token = sign_in(client, accounts["admin"])
    row = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(token)))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/tasks").status_code == 401


def test_csrf_missing_wrong_and_foreign_origin_fail(client, accounts):
    sign_in(client, accounts["admin"])
    csrf = client.headers.pop("X-CSRF-Token")
    payload = {"name": "registered"}
    assert client.post("/api/applications", json=payload).status_code == 403
    assert (
        client.post("/api/applications", json=payload, headers={b"X-CSRF-Token": b"\xff"}).status_code == 403
    )
    assert (
        client.post("/api/applications", json=payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )
    assert (
        client.post(
            "/api/applications",
            json=payload,
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert client.post("/api/applications", json=payload, headers={"X-CSRF-Token": csrf}).status_code == 201


@pytest.mark.parametrize(
    "path,body",
    [
        ("/tasks", {"name": "data_processing_task", "idempotency_key": "viewer-attempt"}),
        ("/workload", {"count": 1}),
        ("/incidents/id/investigate", {"configuration": "FULL_SYSTEM"}),
        ("/incidents/id/verify", None),
        ("/incidents/id/remediate", None),
        ("/remediations/id/approve", None),
        ("/remediations/id/reject", None),
        ("/faults/inject", {"fault_type": "WORKER_FAILURE"}),
        ("/faults/id/reset", None),
        ("/experiments/run", {}),
        ("/accounts", {}),
        ("/applications", {"name": "bad"}),
    ],
)
def test_viewer_cannot_mutate(client, accounts, path, body):
    sign_in(client, accounts["viewer"])
    assert client.get("/api/tasks").status_code == 200
    assert client.post("/api" + path, json=body).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/tasks",
        "/tasks/id",
        "/incidents",
        "/incidents/id",
        "/incidents/id/evidence",
        "/incidents/id/investigation",
        "/approvals",
        "/workers",
        "/queues",
        "/system/health",
        "/metrics/summary",
        "/metrics/timeseries",
        "/history",
        "/faults",
        "/experiments",
        "/experiments/results",
        "/experiments/id/export",
        "/settings",
    ],
)
def test_remote_membership_never_exposes_local_records(client, accounts, path):
    sign_in(client, accounts["outsider"])
    assert client.get("/api" + path).status_code == 403
    visible = client.get("/api/applications").json()
    assert [row["id"] for row in visible] == [accounts["remote"].id]
    assert client.get("/api/applications/" + accounts["other"].id).status_code == 404


def test_operator_can_submit_but_cannot_administer(client, accounts):
    sign_in(client, accounts["operator"])
    assert (
        client.post(
            "/api/tasks", json={"name": "data_processing_task", "idempotency_key": "operator-test-01"}
        ).status_code
        == 202
    )
    assert client.get("/api/accounts").status_code == 403
    assert client.get("/api/access-audit").status_code == 403
    assert client.post("/api/faults/inject", json={"fault_type": "WORKER_FAILURE"}).status_code == 403
    assert client.post("/api/experiments/run", json={}).status_code == 403


def test_approval_records_named_actor(client, accounts, db, incident, diagnosis, monkeypatch):
    from app.remediation.service import propose

    diagnosis.root_cause = "WORKER_FAILURE"
    diagnosis.affected_component = "worker"
    diagnosis.recommended_action = "RESTART_WORKER"
    monkeypatch.setenv("REMEDIATION_MODE", "execute")
    settings.cache_clear()
    remediation = propose(db, incident, diagnosis)
    db.commit()
    sign_in(client, accounts["operator"])
    response = client.post(f"/api/remediations/{remediation.id}/approve")
    assert response.status_code == 200, response.text
    assert response.json()["decided_by"] == accounts["operator"].id
    db.expire_all()
    assert (
        db.scalar(select(ApprovalRequest).where(ApprovalRequest.remediation_id == remediation.id)).status
        == "APPROVED"
    )


def test_role_change_disables_old_sessions_and_last_admin_is_preserved(client, accounts):
    from app.main import app

    with TestClient(app) as operator:
        sign_in(operator, accounts["operator"])
        sign_in(client, accounts["admin"])
        response = client.post(
            f"/api/accounts/{accounts['operator'].id}", json={"role": "viewer", "active": True}
        )
        assert response.status_code == 200
        assert operator.get("/api/tasks").status_code == 401
    for change in ({"role": "viewer", "active": True}, {"role": "admin", "active": False}):
        assert client.post(f"/api/accounts/{accounts['admin'].id}", json=change).status_code == 409
    assert client.get("/api/accounts").status_code == 200


def test_password_change_revokes_all_devices(client, accounts):
    from app.main import app

    with TestClient(app) as second:
        sign_in(second, accounts["viewer"])
        sign_in(client, accounts["viewer"])
        assert (
            client.post(
                "/api/session/password",
                json={"current_password": PASSWORD, "new_password": "a completely new password"},
            ).status_code
            == 200
        )
        assert second.get("/api/tasks").status_code == 401
        assert client.get("/api/tasks").status_code == 401
        assert (
            client.post(
                "/api/session", json={"email": accounts["viewer"].email, "password": PASSWORD}
            ).status_code
            == 401
        )


def test_login_throttle_survives_session_and_settings_changes(client, db, accounts):
    email = accounts["viewer"].email
    for _ in range(10):
        assert client.post("/api/session", json={"email": email, "password": "bad"}).status_code == 401
    settings.cache_clear()
    assert client.post("/api/session", json={"email": email, "password": PASSWORD}).status_code == 429
    assert db.scalar(select(LoginBucket).where(LoginBucket.key == digest("account:" + email))).count == 11


def test_websocket_revocation_and_application_removal(client, db, accounts):
    from app.main import app

    with TestClient(app) as viewer:
        sign_in(viewer, accounts["viewer"])
        with viewer.websocket_connect("/api/ws", headers=ORIGIN) as ws:
            assert ws.receive_json()["type"] == "events"
            sign_in(client, accounts["admin"])
            assert (
                client.post(
                    f"/api/applications/{LOCAL_APPLICATION}/members", json={"account_ids": []}
                ).status_code
                == 200
            )
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        assert viewer.get("/api/tasks").status_code == 403
    sign_in(client, accounts["outsider"])
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/ws", headers=ORIGIN):
        pass


def test_connector_scope_expiry_revocation_and_no_user_authority(client, accounts, db):
    sign_in(client, accounts["admin"])
    key = accounts["remote"].id
    response = client.post(
        f"/api/applications/{key}/credentials", json={"name": "read only", "scopes": ["identity:read"]}
    )
    assert response.status_code == 201, response.text
    credential = response.json()
    token = credential["token"]
    headers = {"Authorization": "Bearer " + token}
    assert token not in client.get(f"/api/applications/{key}/credentials").text
    assert token not in client.get("/api/access-audit").text
    client.cookies.clear()
    assert client.get(f"/api/connector/applications/{key}", headers=headers).status_code == 200
    assert client.post(f"/api/connector/applications/{key}/heartbeat", headers=headers).status_code == 403
    assert (
        client.get(f"/api/connector/applications/{accounts['other'].id}", headers=headers).status_code == 403
    )
    assert client.get("/api/tasks", headers=headers).status_code == 401
    row = db.get(ConnectorCredential, credential["id"])
    assert row.token_hash == digest(token)
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert client.get(f"/api/connector/applications/{key}", headers=headers).status_code == 401
    row.expires_at = now() + timedelta(days=1)
    db.commit()
    sign_in(client, accounts["admin"])
    assert client.delete(f"/api/applications/{key}/credentials/{row.id}").status_code == 200
    assert client.get(f"/api/connector/applications/{key}", headers=headers).status_code == 401




def test_connector_events_are_scoped_deduplicated_and_project_task_state(client, accounts, db):
    from app.db.models import ApplicationEvent, TaskExecution

    sign_in(client, accounts["admin"])
    key = accounts["remote"].id
    response = client.post(
        f"/api/applications/{key}/credentials",
        json={"name": "celery", "scopes": ["events:write"]},
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    client.cookies.clear()

    event = {
        "event_id": "evt-paperless-0001",
        "application_id": key,
        "event_type": "task.started",
        "occurred_at": "2026-09-24T00:00:00Z",
        "task_id": "11111111-1111-1111-1111-111111111111",
        "task_name": "paperless.tasks.consume_file",
        "worker_id": "paperless-worker@host",
        "queue": "celery",
        "correlation_id": "22222222-2222-2222-2222-222222222222",
        "trace_id": "33333333333333333333333333333333",
        "payload": {"state": "STARTED", "secret": "should-be-redacted"},
    }
    accepted = client.post(f"/api/connector/applications/{key}/events", headers=headers, json=event)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    duplicate = client.post(f"/api/connector/applications/{key}/events", headers=headers, json=event)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"

    rows = list(db.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == key)))
    assert len(rows) == 1
    assert rows[0].payload["secret"] == "[REDACTED]"
    assert "should-be-redacted" not in str(rows[0].payload)
    task = db.scalar(
        select(TaskExecution).where(
            TaskExecution.id == event["task_id"],
            TaskExecution.scope_id == key,
        )
    )
    assert task is not None and task.status == "STARTED"

    assert client.post(
        f"/api/connector/applications/{accounts['other'].id}/events", headers=headers, json={**event, "application_id": accounts['other'].id}
    ).status_code == 403
    assert db.scalar(select(ApplicationEvent).where(ApplicationEvent.event_id == event["event_id"])).application_id == key


def test_connector_failed_event_creates_incident_for_application(client, accounts, db):
    from app.db.models import Incident

    sign_in(client, accounts["admin"])
    key = accounts["remote"].id
    token = client.post(
        f"/api/applications/{key}/credentials",
        json={"name": "celery-failures", "scopes": ["events:write"]},
    ).json()["token"]
    client.cookies.clear()
    event = {
        "event_id": "evt-paperless-failure-0001",
        "application_id": key,
        "event_type": "task.failed",
        "occurred_at": "2026-09-24T00:00:00Z",
        "task_id": "44444444-4444-4444-4444-444444444444",
        "task_name": "paperless.tasks.consume_file",
        "worker_id": "paperless-worker@host",
        "queue": "celery",
        "payload": {"exception": "RuntimeError: document processing failed", "retries": 3},
    }
    response = client.post(
        f"/api/connector/applications/{key}/events",
        headers={"Authorization": "Bearer " + token},
        json=event,
    )
    assert response.status_code == 200, response.text
    incidents = list(db.scalars(select(Incident).where(Incident.scope_id == key)))
    assert len(incidents) == 1
    assert incidents[0].component == "task"


def test_connector_heartbeat_cannot_create_operational_evidence(client, accounts):
    sign_in(client, accounts["admin"])
    key = accounts["remote"].id
    response = client.post(
        f"/api/applications/{key}/credentials", json={"name": "contact", "scopes": ["heartbeat:write"]}
    )
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    assert (
        client.post(f"/api/connector/applications/{key}/heartbeat", headers=headers).json()["meaning"]
        == "Connector contact only"
    )
    assert client.get("/api/incidents").json() == []
    assert client.post(f"/api/applications/{key}/disable").status_code == 200
    assert client.post(f"/api/connector/applications/{key}/heartbeat", headers=headers).status_code == 403


def test_metrics_requires_separate_credential(client, accounts, monkeypatch, tmp_path):
    token_file = tmp_path / "metrics-token"
    token_file.write_text("a-separate-metrics-credential")
    monkeypatch.setenv("METRICS_TOKEN_FILE", str(token_file))
    settings.cache_clear()
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={b"Authorization": b"\xff"}).status_code == 401
    assert (
        client.get("/metrics", headers={"Authorization": "Bearer a-separate-metrics-credential"}).status_code
        == 200
    )
    assert client.get("/health").status_code == 200


def test_password_validation_does_not_echo_secret(client, accounts):
    secret = "secret-over-limit-" * 20
    response = client.post("/api/session", json={"email": "user@example.test", "password": secret})
    assert response.status_code == 422 and secret not in response.text


def test_existing_database_migrates_without_losing_records(monkeypatch, tmp_path):
    from alembic import command
    from alembic.config import Config

    from app.core.schemas import TaskInput
    from app.db.models import TaskExecution
    from app.db.session import engine, session_scope
    from app.workers.publisher import create_task

    database = tmp_path / "migration.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(database))
    settings.cache_clear()
    engine.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(config, "0001")
    with session_scope() as db:
        task = create_task(db, TaskInput(name="data_processing_task", idempotency_key="preserve-this-task"))
        task_id = task.id
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with session_scope() as db:
        assert db.get(TaskExecution, task_id).idempotency_key == "preserve-this-task"
        assert db.get(Application, LOCAL_APPLICATION).kind == "local"
        assert list(db.scalars(select(Account))) == []
        assert list(db.scalars(select(AccessAudit))) == []


def test_disabled_account_and_explicit_revocation_block_every_device(client, accounts):
    from app.main import app

    with TestClient(app) as operator:
        sign_in(operator, accounts["operator"])
        sign_in(client, accounts["admin"])
        key = accounts["operator"].id
        assert client.post(f"/api/accounts/{key}/revoke-sessions").status_code == 200
        assert operator.get("/api/tasks").status_code == 401
        sign_in(operator, accounts["operator"])
        assert (
            client.post(f"/api/accounts/{key}", json={"role": "operator", "active": False}).status_code == 200
        )
        assert operator.get("/api/tasks").status_code == 401
        assert (
            operator.post(
                "/api/session", json={"email": accounts["operator"].email, "password": PASSWORD}
            ).status_code
            == 401
        )


def test_cross_application_member_cannot_approve_local_action(client, accounts):
    sign_in(client, accounts["outsider"])
    assert client.post("/api/remediations/known-id/approve").status_code == 403
    assert client.post("/api/incidents/known-id/verify").status_code == 403


def test_downgraded_role_is_enforced_after_new_login(client, accounts):
    sign_in(client, accounts["admin"])
    key = accounts["operator"].id
    assert client.post(f"/api/accounts/{key}", json={"role": "viewer", "active": True}).status_code == 200
    sign_in(client, accounts["operator"])
    assert client.get("/api/tasks").status_code == 200
    assert (
        client.post(
            "/api/tasks", json={"name": "data_processing_task", "idempotency_key": "downgraded-role"}
        ).status_code
        == 403
    )
