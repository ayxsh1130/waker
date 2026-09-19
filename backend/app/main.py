import asyncio
import logging

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.routes import router
from app.core.auth import COOKIE, origins, valid_session
from app.core.auth import router as auth_router
from app.core.config import settings
from app.core.safety import redact
from app.db.models import StreamEvent
from app.db.session import row_dict, session_scope
from app.observability.metrics import exposition
from app.observability.telemetry import configure_telemetry

app = FastAPI(
    title="AutoPilot",
    version="1.0.0",
    description="Local incident investigation and controlled remediation research prototype",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(auth_router)
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
def metrics():
    return Response(exposition(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.websocket("/api/ws")
async def websocket(websocket: WebSocket):
    if websocket.headers.get("origin") not in origins() or (
        settings().app_auth_enabled and not valid_session(websocket.cookies.get(COOKIE, ""))
    ):
        await websocket.close(code=1008)
        return
    await websocket.accept()

    def read(cursor):
        with session_scope() as db:
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
            await websocket.send_json({"type": "events", "events": events, "cursor": cursor})
            await asyncio.sleep(2)
    except (WebSocketDisconnect, RuntimeError):
        pass
