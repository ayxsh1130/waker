import asyncio
import hmac
import os
import signal
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException

process = None
lock = asyncio.Lock()


def internal(x_control_token: str = Header(default="")):
    path = Path(os.getenv("INTERNAL_TOKEN_FILE", "/run/autopilot/control_token"))
    secret = os.getenv("INTERNAL_TOKEN", "") or (path.read_text().strip() if path.exists() else "")
    if not secret or not hmac.compare_digest(secret, x_control_token):
        raise HTTPException(403, "Control authorization required")


def start_worker():
    global process
    if process is not None and process.poll() is None:
        return
    process = subprocess.Popen(
        [
            "celery",
            "-A",
            "app.workers.celery_app:celery_app",
            "worker",
            "-n",
            "experiment@autopilot",
            "-Q",
            "experiment",
            "--concurrency=2",
            "--loglevel=INFO",
            "--heartbeat-interval=2",
        ],
        start_new_session=True,
    )


@asynccontextmanager
async def lifespan(app):
    start_worker()
    yield
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.to_thread(process.wait, timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)


app = FastAPI(title="Isolated experiment worker supervisor", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "HEALTHY", "meaning": "Supervisor process liveness; /status reports child worker"}


@app.get("/status")
def status():
    return {
        "worker": "experiment@autopilot",
        "running": bool(process and process.poll() is None),
    }


@app.post("/crash", dependencies=[Depends(internal)])
async def crash():
    async with lock:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            await asyncio.to_thread(process.wait, timeout=5)
    return status()


@app.post("/restart", dependencies=[Depends(internal)])
async def restart():
    async with lock:
        start_worker()
    return status()
