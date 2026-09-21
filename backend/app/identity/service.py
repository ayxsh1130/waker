"""Account and credential operations; no workload or fault-injection authority."""

import hashlib
import secrets
import time
from datetime import UTC, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.base import now, uid
from app.db.models import (
    AccessAudit,
    Account,
    Application,
    ApplicationMember,
    LoginBucket,
    LoginSession,
    RuntimeSetting,
)
from app.db.session import session_scope

LOCAL_APPLICATION = "00000000-0000-0000-0000-000000000001"
ROLES = {"viewer", "operator", "admin"}
passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def future(value):
    return value.replace(tzinfo=UTC) > now()


def normalize_email(value):
    value = value.strip().lower()
    if (
        len(value) > 254
        or value.count("@") != 1
        or not value.isprintable()
        or any(c.isspace() for c in value)
    ):
        raise ValueError("Enter a valid email address")
    local, domain = value.split("@")
    if not local or not domain or "." not in domain:
        raise ValueError("Enter a valid email address")
    return value


def hash_password(value):
    if not 15 <= len(value) <= 128:
        raise ValueError("Use a password of 15 to 128 characters")
    return passwords.hash(value)


def password_matches(stored, supplied):
    try:
        return passwords.verify(stored, supplied)
    except (VerificationError, InvalidHashError):
        return False


def insert_for(db, model):
    return (sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert)(model)


def ensure_local_application(db):
    db.execute(
        insert_for(db, Application)
        .values(id=LOCAL_APPLICATION, name="Local workload", kind="local", active=True, created_at=now())
        .on_conflict_do_nothing(index_elements=[Application.id])
    )


def lock_administration(db):
    # A single row serializes bootstrap and last-administrator checks across API processes.
    db.execute(
        insert_for(db, RuntimeSetting)
        .values(key="identity_admin_lock", value={}, updated_at=now())
        .on_conflict_do_nothing(index_elements=[RuntimeSetting.key])
    )
    db.execute(
        update(RuntimeSetting).where(RuntimeSetting.key == "identity_admin_lock").values(updated_at=now())
    )


def audit(db, actor, action, target, **details):
    db.add(AccessAudit(actor=actor, action=action, target=target, details=details))


def user_view(account):
    return {"id": account.id, "email": account.email, "role": account.role, "active": account.active}


def create_account(db, email, password, role="viewer", applications=(), actor="console", bootstrap=False):
    lock_administration(db)
    if bootstrap and db.scalar(select(func.count()).select_from(Account)):
        raise ValueError("Accounts already exist; bootstrap is only available on a new installation")
    if role not in ROLES:
        raise ValueError("Unknown account role")
    ensure_local_application(db)
    for app_id in set(applications):
        app = db.get(Application, app_id)
        if not app or not app.active:
            raise ValueError("Unknown or disabled application")
    account = Account(
        id=uid(), email=normalize_email(email), password_hash=hash_password(password), role=role
    )
    db.add(account)
    db.flush()
    for app_id in set(applications):
        db.add(ApplicationMember(account_id=account.id, application_id=app_id))
    audit(db, actor, "account.created", account.id, role=role, applications=list(applications))
    return account


def revoke_sessions(db, account_id):
    db.execute(
        update(LoginSession)
        .where(LoginSession.account_id == account_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=now())
    )


def update_account(db, account, role, active, actor):
    lock_administration(db)
    db.refresh(account)
    if account.role == "admin" and account.active and (role != "admin" or not active):
        admins = db.scalar(
            select(func.count()).select_from(Account).where(Account.active.is_(True), Account.role == "admin")
        )
        if admins <= 1:
            raise HTTPException(409, "Keep at least one active administrator")
    if account.role != role or account.active != active:
        revoke_sessions(db, account.id)
    account.role, account.active = role, active
    audit(db, actor, "account.updated", account.id, role=role, active=active)


def throttle_login(address, email):
    # Atomic, database-backed counters remain effective across replicas and restarts.
    # Count successful attempts too; do not reset a shared counter on success.
    window = int(time.time()) // 300
    blocked = False
    with session_scope() as db:
        for category, value, limit in (("ip", address, 30), ("account", email, 10)):
            key = digest(category + ":" + value)
            statement = insert_for(db, LoginBucket).values(key=key, window=window, count=1)
            statement = statement.on_conflict_do_update(
                index_elements=[LoginBucket.key],
                set_={
                    "window": window,
                    "count": case((LoginBucket.window == window, LoginBucket.count + 1), else_=1),
                },
            ).returning(LoginBucket.count)
            blocked |= db.scalar(statement) > limit
            if blocked:
                break
        db.execute(delete(LoginBucket).where(LoginBucket.window < window - 1))
    if blocked:
        raise HTTPException(
            429, "Too many sign-in attempts; retry in five minutes", headers={"Retry-After": "300"}
        )


def new_session(db, account, ttl):
    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            account_id=account.id, token_hash=digest(token), expires_at=now() + timedelta(seconds=ttl)
        )
    )
    return token
