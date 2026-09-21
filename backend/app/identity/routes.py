import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import delete, select

from app.core.auth import (
    has_application,
    require_admin,
    require_admin_write,
    require_identity,
)
from app.core.schemas import StrictModel
from app.db.base import now
from app.db.models import (
    AccessAudit,
    Account,
    Application,
    ApplicationMember,
    ConnectorCredential,
)
from app.db.session import get_db
from app.identity.service import (
    LOCAL_APPLICATION,
    audit,
    create_account,
    digest,
    future,
    revoke_sessions,
    update_account,
    user_view,
)

router = APIRouter(prefix="/api", tags=["Accounts and applications"])


class AccountInput(StrictModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=15, max_length=128, repr=False)
    role: Literal["viewer", "operator", "admin"] = "viewer"
    application_ids: list[str] = Field(default_factory=list, max_length=100)


class AccountUpdate(StrictModel):
    role: Literal["viewer", "operator", "admin"]
    active: bool


class ApplicationInput(StrictModel):
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")


class MembersInput(StrictModel):
    account_ids: list[str] = Field(max_length=100)


class CredentialInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["identity:read", "heartbeat:write"]] = Field(min_length=1, max_length=2)
    expires_in_days: int = Field(default=30, ge=1, le=365)


def application_view(app):
    return {
        "id": app.id,
        "name": app.name,
        "active": app.active,
        "kind": app.kind,
        "operational_connection": "local" if app.kind == "local" else "not_connected",
    }


def credential_view(record):
    return {
        key: getattr(record, key)
        for key in (
            "id",
            "application_id",
            "name",
            "scopes",
            "created_at",
            "expires_at",
            "revoked_at",
            "last_seen_at",
        )
    }


def required(db, model, key):
    row = db.get(model, key)
    if not row:
        raise HTTPException(404, "Record not found")
    return row


@router.get("/accounts", dependencies=[Depends(require_admin)])
def accounts(db=Depends(get_db, scope="function")):
    return [user_view(a) for a in db.scalars(select(Account).order_by(Account.created_at.desc()))]


@router.post("/accounts", status_code=201)
def add_account(body: AccountInput, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")):
    return user_view(create_account(db, body.email, body.password, body.role, body.application_ids, actor.id))


@router.post("/accounts/{key}")
def edit_account(
    key: str, body: AccountUpdate, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")
):
    account = required(db, Account, key)
    update_account(db, account, body.role, body.active, actor.id)
    return user_view(account)


@router.post("/accounts/{key}/revoke-sessions")
def revoke_account_sessions(
    key: str, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")
):
    account = required(db, Account, key)
    revoke_sessions(db, account.id)
    audit(db, actor.id, "sessions.revoked", account.id)
    return {"revoked": True}


@router.get("/access-audit", dependencies=[Depends(require_admin)])
def access_audit(db=Depends(get_db, scope="function")):
    return [
        {key: getattr(r, key) for key in ("id", "created_at", "actor", "action", "target", "details")}
        for r in db.scalars(select(AccessAudit).order_by(AccessAudit.created_at.desc()).limit(200))
    ]


@router.get("/applications")
def applications(actor=Depends(require_identity), db=Depends(get_db, scope="function")):
    return [
        application_view(app)
        for app in db.scalars(select(Application).order_by(Application.created_at.desc()))
        if has_application(db, actor, app.id)
    ]


@router.post("/applications", status_code=201)
def add_application(
    body: ApplicationInput, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")
):
    app = Application(name=body.name.strip(), kind="registered")
    db.add(app)
    db.flush()
    audit(db, actor.id, "application.created", app.id)
    return application_view(app)


@router.get("/applications/{key}")
def application(key: str, actor=Depends(require_identity), db=Depends(get_db, scope="function")):
    if not has_application(db, actor, key):
        raise HTTPException(404, "Application not found")
    return application_view(required(db, Application, key))


@router.post("/applications/{key}/disable")
def disable_application(key: str, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")):
    app = required(db, Application, key)
    if key == LOCAL_APPLICATION:
        raise HTTPException(409, "Stop the local controller through deployment controls")
    app.active = False
    audit(db, actor.id, "application.disabled", key)
    return application_view(app)


@router.get("/applications/{key}/members", dependencies=[Depends(require_admin)])
def members(key: str, db=Depends(get_db, scope="function")):
    required(db, Application, key)
    return list(
        db.scalars(select(ApplicationMember.account_id).where(ApplicationMember.application_id == key))
    )


@router.post("/applications/{key}/members")
def set_members(
    key: str, body: MembersInput, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")
):
    required(db, Application, key)
    for account_id in set(body.account_ids):
        required(db, Account, account_id)
    db.execute(delete(ApplicationMember).where(ApplicationMember.application_id == key))
    for account_id in set(body.account_ids):
        db.add(ApplicationMember(application_id=key, account_id=account_id))
    audit(db, actor.id, "application.members.changed", key, account_ids=sorted(set(body.account_ids)))
    return {"account_ids": sorted(set(body.account_ids))}


@router.get("/applications/{key}/credentials", dependencies=[Depends(require_admin)])
def credentials(key: str, db=Depends(get_db, scope="function")):
    required(db, Application, key)
    return [
        credential_view(r)
        for r in db.scalars(
            select(ConnectorCredential)
            .where(ConnectorCredential.application_id == key)
            .order_by(ConnectorCredential.created_at.desc())
        )
    ]


@router.post("/applications/{key}/credentials", status_code=201)
def issue_credential(
    key: str, body: CredentialInput, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")
):
    app = required(db, Application, key)
    if not app.active or app.kind == "local":
        raise HTTPException(409, "Credentials are available for active registered applications")
    token = "wkr_" + secrets.token_urlsafe(32)
    record = ConnectorCredential(
        application_id=key,
        name=body.name,
        token_hash=digest(token),
        scopes=sorted(set(body.scopes)),
        expires_at=now() + timedelta(days=body.expires_in_days),
    )
    db.add(record)
    db.flush()
    audit(db, actor.id, "credential.created", record.id, application_id=key, scopes=record.scopes)
    return {**credential_view(record), "token": token}


@router.delete("/applications/{key}/credentials/{credential_id}")
def revoke_credential(
    key: str, credential_id: str, actor=Depends(require_admin_write), db=Depends(get_db, scope="function")
):
    record = required(db, ConnectorCredential, credential_id)
    if record.application_id != key:
        raise HTTPException(404, "Credential not found")
    record.revoked_at = now()
    audit(db, actor.id, "credential.revoked", record.id, application_id=key)
    return credential_view(record)


def connector(db, request, app_id, scope):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer wkr_") or len(authorization) > 100:
        raise HTTPException(401, "Connector credential required")
    record = db.scalar(
        select(ConnectorCredential).where(ConnectorCredential.token_hash == digest(authorization[7:]))
    )
    if not record or record.revoked_at or not future(record.expires_at):
        raise HTTPException(401, "Invalid connector credential")
    app = db.get(Application, record.application_id)
    if not app or not app.active or record.application_id != app_id or scope not in record.scopes:
        raise HTTPException(403, "Credential does not permit this application or operation")
    return record, app


@router.get("/connector/applications/{key}")
def connector_identity(key: str, request: Request, db=Depends(get_db, scope="function")):
    _, app = connector(db, request, key, "identity:read")
    return application_view(app)


@router.post("/connector/applications/{key}/heartbeat")
def connector_heartbeat(key: str, request: Request, db=Depends(get_db, scope="function")):
    record, _ = connector(db, request, key, "heartbeat:write")
    record.last_seen_at = now()
    return {"application_id": key, "received_at": record.last_seen_at, "meaning": "Connector contact only"}
