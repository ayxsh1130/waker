import asyncio
import hmac
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.routes import router
from app.core.auth import COOKIE, has_application, origins, resolve_session
from app.core.auth import router as auth_router
from app.core.config import settings
from app.core.safety import redact
from app.db.models import StreamEvent
from app.db.session import row_dict, session_scope
from app.identity.routes import router as identity_router
from app.identity.service import LOCAL_APPLICATION
from app.observability.metrics import exposition
from app.observability.telemetry import configure_telemetry

app = FastAPI(
    title="Waker",
    version="1.0.0",
    description="Local incident investigation and controlled remediation research prototype",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
)
app.include_router(auth_router)
app.include_router(identity_router)
app.include_router(router)
configure_telemetry("autopilot-api")

FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")


@app.middleware("http")
async def headers(request: Request, call_next):
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        return JSONResponse({"detail": "Invalid content length"}, 400)
    if size > 100000:
        return JSONResponse({"detail": "Request too large"}, 413)
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        }
    )
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"detail": redact(str(exc))}, 422)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default response includes the submitted input, including passwords.
    return JSONResponse(
        {"detail": [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]}, 422
    )


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse({"detail": "Request conflicts with a current or existing record"}, 409)


@app.exception_handler(Exception)
async def failed(request, exc):
    logging.getLogger(__name__).error("Request failed: %s", type(exc).__name__)
    return JSONResponse({"detail": "Service operation failed", "error_type": type(exc).__name__}, 503)


@app.get("/health")
def health():
    return {
        "status": "HEALTHY",
        "service": "autopilot-api",
        "meaning": "Process liveness; see /api/system/health for dependencies",
    }


@app.get("/metrics")
def metrics(request: Request):
    if settings().app_auth_enabled:
        path = Path(settings().metrics_token_file)
        secret = path.read_text().strip() if path.is_file() else ""
        supplied = request.headers.get("Authorization", "")
        if not secret or not hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode()):
            raise HTTPException(401, "Metrics credential required")
    return Response(exposition(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.websocket("/api/ws")
async def websocket(websocket: WebSocket):
    token = websocket.cookies.get(COOKIE, "")

    def authorized():
        with session_scope() as db:
            principal = resolve_session(db, token)
            return principal is not None and has_application(db, principal, LOCAL_APPLICATION)

    if websocket.headers.get("origin") not in origins() or not await asyncio.to_thread(authorized):
        await websocket.close(code=1008)
        return
    await websocket.accept()

    def read(cursor):
        with session_scope() as db:
            principal = resolve_session(db, token)
            if not principal or not has_application(db, principal, LOCAL_APPLICATION):
                return None, cursor
            if cursor is None:
                return [], db.scalar(select(func.max(StreamEvent.id))) or 0
            rows = list(
                db.scalars(
                    select(StreamEvent).where(StreamEvent.id > cursor).order_by(StreamEvent.id).limit(200)
                )
            )
            return [redact(row_dict(r)) for r in rows], rows[-1].id if rows else cursor

    cursor = None
    try:
        while True:
            events, cursor = await asyncio.to_thread(read, cursor)
            if events is None:
                await websocket.close(code=1008)
                return
            await websocket.send_json({"type": "events", "events": events, "cursor": cursor})
            await asyncio.sleep(2)
    except (WebSocketDisconnect, RuntimeError):
        pass
