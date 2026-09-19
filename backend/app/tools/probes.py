import time

import httpx
import redis
from sqlalchemy import text

from app.core.config import settings
from app.db.session import session_scope


def redis_status(url=None):
    started = time.monotonic()
    client = redis.Redis.from_url(url or settings().redis_url, socket_timeout=2, socket_connect_timeout=2)
    try:
        client.ping()
        info = client.info("clients")
        return {
            "status": "HEALTHY",
            "connected_clients": info.get("connected_clients"),
            "latency_ms": (time.monotonic() - started) * 1000,
        }
    except redis.RedisError as exc:
        return {"status": "UNAVAILABLE", "error_type": type(exc).__name__}
    finally:
        client.close()


def database_status():
    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
            result = {"status": "HEALTHY"}
            if db.bind.dialect.name == "postgresql":
                result["connections"] = db.scalar(text("SELECT count(*) FROM pg_stat_activity"))
            return result
    except Exception as exc:
        return {"status": "UNAVAILABLE", "error_type": type(exc).__name__}


def external_status(scope):
    cfg = settings()
    outcomes = []
    with httpx.Client(timeout=2, follow_redirects=False) as client:
        for _ in range(2):
            start = time.monotonic()
            try:
                response = client.get(cfg.dependency_url + "/work/" + scope)
                outcomes.append(
                    {"status_code": response.status_code, "duration_seconds": time.monotonic() - start}
                )
            except httpx.HTTPError as exc:
                outcomes.append(
                    {"error_type": type(exc).__name__, "duration_seconds": time.monotonic() - start}
                )
        try:
            response = client.get(cfg.dependency_url + "/observations/" + scope)
            response.raise_for_status()
            observed = response.json().get("requests", [])
        except (httpx.HTTPError, ValueError):
            observed = []
    return {
        "status": "HEALTHY" if all(v.get("status_code") == 200 for v in outcomes) else "DEGRADED",
        "probes": outcomes,
        "observations": observed,
    }


def queue_metrics():
    client = redis.Redis.from_url(settings().redis_url, socket_timeout=2, socket_connect_timeout=2)
    try:
        depths = {
            q: sum(client.llen(q + suffix) for suffix in ["", "\x06\x163", "\x06\x166", "\x06\x169"])
            for q in ["default", "experiment"]
        }
        return {"status": "HEALTHY", "depths": depths}
    except redis.RedisError as exc:
        return {"status": "UNAVAILABLE", "depths": {}, "error_type": type(exc).__name__}
    finally:
        client.close()


def http_health(url, path="/health"):
    try:
        with httpx.Client(timeout=2, follow_redirects=False) as client:
            response = client.get(url.rstrip("/") + path)
            response.raise_for_status()
        return {"status": "HEALTHY"}
    except httpx.HTTPError as exc:
        return {"status": "UNAVAILABLE", "error_type": type(exc).__name__}
