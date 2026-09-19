import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

state = {"enabled": True}
connections = set()


async def pump(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        return


async def forward(reader, writer):
    if not state["enabled"]:
        writer.close()
        return
    upstream = None
    try:
        remote, upstream = await asyncio.wait_for(
            asyncio.open_connection(os.getenv("REDIS_HOST", "redis"), int(os.getenv("REDIS_PORT", "6379"))),
            timeout=3,
        )
        connections.update([writer, upstream])
        tasks = [
            asyncio.create_task(pump(reader, upstream)),
            asyncio.create_task(pump(remote, writer)),
        ]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, TimeoutError):
        return
    finally:
        for stream in [writer, upstream]:
            if stream:
                connections.discard(stream)
                stream.close()


@asynccontextmanager
async def lifespan(app):
    server = await asyncio.start_server(forward, "0.0.0.0", int(os.getenv("PROXY_PORT", "16379")))
    yield
    server.close()
    await server.wait_closed()
    for connection in list(connections):
        connection.close()


app = FastAPI(title="Fixed experiment broker proxy", lifespan=lifespan)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@app.get("/health")
def health():
    return {"status": "HEALTHY"}


@app.post("/proxies/broker")
async def control(body: Control, x_control_token: str = Header(default="")):
    path = Path(os.getenv("INTERNAL_TOKEN_FILE", "/run/autopilot/control_token"))
    expected = os.getenv("INTERNAL_TOKEN", "") or (path.read_text().strip() if path.exists() else "")
    if not expected or not hmac.compare_digest(expected, x_control_token):
        raise HTTPException(403, "Control authorization required")
    state["enabled"] = body.enabled
    if not body.enabled:
        for connection in list(connections):
            connection.close()
    return {"enabled": state["enabled"]}
