import hmac
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import select

from app.core.config import settings
from app.core.schemas import StrictModel
from app.db.base import now
from app.db.models import Account, Application, ApplicationMember, LoginSession
from app.db.session import get_db
from app.identity.service import (
    DUMMY_HASH,
    LOCAL_APPLICATION,
    audit,
    digest,
    future,
    hash_password,
    new_session,
    normalize_email,
    password_matches,
    passwords,
    revoke_sessions,
    throttle_login,
    user_view,
)

COOKIE = "waker_session"
router = APIRouter(prefix="/api/session", tags=["Session"])


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    email: str
    development: bool = False


def origins():
    return {o.strip() for o in settings().cors_allowed_origins.split(",") if o.strip()} | {
        settings().frontend_url
    }


def check_origin(request):
    if request.headers.get("origin") and request.headers["origin"] not in origins():
        raise HTTPException(403, "Origin is not allowed")


def csrf_token(token):
    return digest("waker-csrf:" + token)


def resolve_session(db, token):
    if not settings().app_auth_enabled:
        return Principal("local-admin", "admin", "local-admin", True)
    if not token or len(token) > 100:
        return None
    record = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(token)))
    if not record or record.revoked_at or not future(record.expires_at):
        return None
    account = db.get(Account, record.account_id)
    if not account or not account.active:
        return None
    return Principal(account.id, account.role, account.email)


def require_identity(request: Request, db=Depends(get_db, scope="function")):
    principal = resolve_session(db, request.cookies.get(COOKIE, ""))
    if not principal:
        raise HTTPException(401, "Sign in required")
    return principal


def has_application(db, principal, app_id):
    if principal.development:
        return app_id == LOCAL_APPLICATION
    application = db.get(Application, app_id)
    return bool(
        application
        and application.active
        and (principal.role == "admin" or db.get(ApplicationMember, (app_id, principal.id)))
    )


def require_session(principal=Depends(require_identity), db=Depends(get_db, scope="function")):
    # Every legacy task, incident, tool, experiment and stream belongs to the local workload.
    if not has_application(db, principal, LOCAL_APPLICATION):
        raise HTTPException(403, "You do not have access to the local workload")
    return principal.id


def require_account_write(request: Request, principal=Depends(require_identity)):
    check_origin(request)
    if not principal.development and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", "").encode(), csrf_token(request.cookies.get(COOKIE, "")).encode()
    ):
        raise HTTPException(403, "Refresh your session before submitting this request")
    return principal


def require_write(principal=Depends(require_account_write), actor=Depends(require_session)):
    if principal.role not in {"admin", "operator"}:
        raise HTTPException(403, "Operator permission required")
    return actor


def require_admin(principal=Depends(require_identity)):
    if principal.development:
        raise HTTPException(403, "Enable account authentication to manage access")
    if principal.role != "admin":
        raise HTTPException(403, "Administrator permission required")
    return principal


def require_admin_write(principal=Depends(require_admin), _=Depends(require_account_write)):
    return principal


class LoginInput(StrictModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class PasswordInput(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=15, max_length=128)


@router.get("")
def session(request: Request, db=Depends(get_db, scope="function")):
    token = request.cookies.get(COOKIE, "")
    principal = resolve_session(db, token)
    return {
        "auth_enabled": settings().app_auth_enabled,
        "authenticated": principal is not None,
        "csrf_token": csrf_token(token) if principal and not principal.development else None,
        "user": {"id": principal.id, "email": principal.email, "role": principal.role} if principal else None,
        "local_access": has_application(db, principal, LOCAL_APPLICATION) if principal else False,
    }


@router.post("")
def login_json(body: LoginInput, request: Request, response: Response, db=Depends(get_db, scope="function")):
    check_origin(request)
    if not settings().app_auth_enabled:
        raise HTTPException(409, "Account authentication is disabled")
    throttle_login(request.client.host if request.client else "unknown", body.email)
    account = db.scalar(select(Account).where(Account.email == body.email))
    matched = password_matches(account.password_hash if account else DUMMY_HASH, body.password)
    if not account or not matched or not account.active:
        # Commit failures deliberately, so rejecting the HTTP request cannot erase the audit.
        audit(db, "anonymous", "login.failed", "session")
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    if passwords.check_needs_rehash(account.password_hash):
        account.password_hash = hash_password(body.password)
    old = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == digest(request.cookies.get(COOKIE, "")))
    )
    if old:
        old.revoked_at = now()
    token = new_session(db, account, settings().session_ttl_seconds)
    audit(db, account.id, "login.succeeded", account.id)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings().cookie_secure,
        samesite="strict",
        max_age=settings().session_ttl_seconds,
        path="/",
    )
    return {"authenticated": True, "user": user_view(account), "csrf_token": csrf_token(token)}


@router.delete("")
def logout(
    request: Request,
    response: Response,
    principal=Depends(require_account_write),
    db=Depends(get_db, scope="function"),
):
    record = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == digest(request.cookies.get(COOKIE, "")))
    )
    if record:
        record.revoked_at = now()
        audit(db, principal.id, "logout", principal.id)
    response.delete_cookie(
        COOKIE, path="/", secure=settings().cookie_secure, httponly=True, samesite="strict"
    )
    return {"authenticated": False}


@router.post("/password")
def change_password(
    body: PasswordInput,
    request: Request,
    response: Response,
    principal=Depends(require_account_write),
    db=Depends(get_db, scope="function"),
):
    if principal.development:
        raise HTTPException(409, "Account authentication is disabled")
    throttle_login(request.client.host if request.client else "unknown", principal.email)
    account = db.get(Account, principal.id)
    if not password_matches(account.password_hash, body.current_password):
        raise HTTPException(401, "Current password is incorrect")
    account.password_hash = hash_password(body.new_password)
    revoke_sessions(db, account.id)
    audit(db, principal.id, "password.changed", account.id)
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False, "message": "Password changed. Sign in again on each device."}


def require_admin_operation(principal=Depends(require_identity), actor=Depends(require_write)):
    if principal.role != "admin":
        raise HTTPException(403, "Administrator permission required for fault and experiment controls")
    return actor
