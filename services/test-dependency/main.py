import asyncio
import hmac
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="Controlled test dependency")
profiles = {}
observations = {}


def internal(x_control_token: str = Header(default="")):
    path = Path(os.getenv("INTERNAL_TOKEN_FILE", "/run/autopilot/control_token"))
    expected = os.getenv("INTERNAL_TOKEN", "") or (path.read_text().strip() if path.exists() else "")
    if not expected or not hmac.compare_digest(expected, x_control_token):
        raise HTTPException(403, "Control authorization required")


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["healthy", "delay", "timeout", "http500", "intermittent"] = "healthy"
    ttl: int = Field(40, ge=10, le=120)
    delay: float = Field(0, ge=0, le=12)
    exception: bool = False
    invalid_integer: bool = False
    database_unavailable: bool = False
    connection_failure: bool = False
    revision: Literal["v1", "v2"] = "v1"


def current(scope):
    profile = profiles.get(scope, {})
    return profile if profile.get("expires", 0) > time.monotonic() else {}


@app.get("/health")
def health():
    return {"status": "HEALTHY"}


@app.put("/internal/profile/{scope}", dependencies=[Depends(internal)])
def configure(scope: str, profile: Profile):
    if len(scope) > 36:
        raise HTTPException(422, "Invalid scope")
    profiles[scope] = {
        **profile.model_dump(),
        "expires": time.monotonic() + profile.ttl,
        "calls": 0,
    }
    observations[scope] = []
    return {"configured": True}


@app.delete("/internal/profile/{scope}", dependencies=[Depends(internal)])
def clear(scope: str):
    profiles.pop(scope, None)
    return {"cleared": True}


@app.get("/internal/profile/{scope}", dependencies=[Depends(internal)])
def profile(scope: str):
    return {k: v for k, v in current(scope).items() if k not in {"calls", "expires", "mode"}}


@app.get("/observations/{scope}")
def observed(scope: str):
    return {"requests": observations.get(scope, [])[-20:]}


@app.get("/work/{scope}")
async def work(scope: str, response: Response, x_correlation_id: str = Header(default="")):
    profile = current(scope)
    profile["calls"] = profile.get("calls", 0) + 1
    started = time.monotonic()
    mode = profile.get("mode", "healthy")
    if mode in {"timeout", "delay"}:
        await asyncio.sleep(5 if mode == "timeout" else 1)
    status = 500 if mode == "http500" or (mode == "intermittent" and profile["calls"] % 2 == 1) else 200
    response.status_code = status
    history = observations.setdefault(scope, [])
    history.append(
        {
            "at": time.time(),
            "duration_seconds": round(time.monotonic() - started, 4),
            "status_code": status,
            "correlation_id": x_correlation_id[:36],
        }
    )
    del history[:-100]
    return {
        "status": "ok" if status == 200 else "dependency unavailable",
        "processed": status == 200,
    }
