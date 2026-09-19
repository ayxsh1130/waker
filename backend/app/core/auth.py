import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field

from app.core.config import settings
from app.core.schemas import StrictModel

COOKIE = "autopilot_session"
attempts = defaultdict(list)
router = APIRouter(prefix="/api/session", tags=["Session"])


def origins():
    return {o.strip() for o in settings().cors_allowed_origins.split(",") if o.strip()} | {
        settings().frontend_url
    }


def check_origin(request):
    if request.headers.get("origin") and request.headers["origin"] not in origins():
        raise HTTPException(403, "Origin is not allowed")


def issue_session():
    payload = str(int(time.time())) + ":" + secrets.token_urlsafe(24)
    signature = hmac.new(
        settings().admin_token.get_secret_value().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return payload + ":" + signature


def valid_session(value):
    try:
        timestamp, nonce, signature = value.split(":")
        age = time.time() - int(timestamp)
        expected = hmac.new(
            settings().admin_token.get_secret_value().encode(),
            (timestamp + ":" + nonce).encode(),
            hashlib.sha256,
        ).hexdigest()
        return 0 <= age <= settings().session_ttl_seconds and hmac.compare_digest(expected, signature)
    except (ValueError, AttributeError):
        return False


def require_session(request: Request):
    if settings().app_auth_enabled and not valid_session(request.cookies.get(COOKIE, "")):
        raise HTTPException(401, "Sign in required")
    return "local-admin" if not settings().app_auth_enabled else "authenticated-admin"


def require_write(request: Request, actor=Depends(require_session)):
    check_origin(request)
    return actor


def login(request, response, token):
    check_origin(request)
    cfg = settings()
    if not cfg.app_auth_enabled:
        return
    address = request.client.host if request.client else "unknown"
    stamp = time.monotonic()
    if len(attempts) > 1000:
        attempts.clear()
    attempts[address] = [t for t in attempts[address] if stamp - t < 300]
    if len(attempts[address]) >= 10:
        raise HTTPException(429, "Too many sign-in attempts; wait five minutes")
    attempts[address].append(stamp)
    if not hmac.compare_digest(cfg.admin_token.get_secret_value(), token):
        raise HTTPException(401, "Invalid application token")
    attempts.pop(address, None)
    response.set_cookie(
        COOKIE,
        issue_session(),
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="strict",
        max_age=cfg.session_ttl_seconds,
        path="/",
    )


class LoginInput(StrictModel):
    token: str = Field(max_length=500)


@router.get("")
def session(request: Request):
    return {
        "auth_enabled": settings().app_auth_enabled,
        "authenticated": not settings().app_auth_enabled or valid_session(request.cookies.get(COOKIE, "")),
    }


@router.post("")
def login_json(body: LoginInput, request: Request, response: Response):
    login(request, response, body.token)
    return {"authenticated": True}


@router.post("/login")
async def login_form(request: Request):
    body = await request.body()
    if len(body) > 2000:
        raise HTTPException(413, "Form is too large")
    values = parse_qs(body.decode("utf-8", errors="replace"))
    response = RedirectResponse(settings().frontend_url, status_code=303)
    login(request, response, values.get("token", [""])[0])
    return response


@router.delete("", dependencies=[Depends(require_write)])
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}
