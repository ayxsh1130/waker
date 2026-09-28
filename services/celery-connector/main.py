import json
import os
import time
import uuid

import requests
from celery import Celery
from celery.events import EventReceiver
from celery.exceptions import OperationalError

WAKER_URL = os.environ["WAKER_URL"].rstrip("/")
APPLICATION_ID = os.environ["WAKER_APPLICATION_ID"]
TOKEN = os.environ["WAKER_CONNECTOR_TOKEN"]
BROKER_URL = os.environ["CELERY_BROKER_URL"]

EVENT_MAP = {
    "task-sent": "task.sent",
    "task-received": "task.received",
    "task-started": "task.started",
    "task-succeeded": "task.succeeded",
    "task-failed": "task.failed",
    "task-retried": "task.retried",
    "task-revoked": "task.revoked",
    "worker-online": "worker.online",
    "worker-heartbeat": "worker.heartbeat",
    "worker-offline": "worker.offline",
}

app = Celery("waker-celery-connector", broker=BROKER_URL)


def normalize(event_type, event):
    normalized = EVENT_MAP.get(event_type)
    if not normalized:
        return None

    occurred_at = event.get("timestamp")
    try:
        occurred_at = float(occurred_at)
    except (TypeError, ValueError):
        occurred_at = time.time()

    # Only forward operational metadata. Do not forward args, kwargs, result,
    # or arbitrary broker payloads because they may contain sensitive data.
    payload = {}
    for key in ("state", "exception", "retries", "runtime", "clock", "pid"):
        if key in event and event[key] is not None:
            value = event[key]
            if isinstance(value, (str, int, float, bool)):
                payload[key] = value

    sequence = event.get("clock", "")
    retry_number = event.get("retries", "")
    stable_key = f"{APPLICATION_ID}:{event_type}:{event.get('uuid', '')}:{event.get('hostname', '')}:{sequence}:{retry_number}"
    return {
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key)),
        "application_id": APPLICATION_ID,
        "event_type": normalized,
        "occurred_at": occurred_at,
        "task_id": event.get("uuid"),
        "task_name": event.get("name"),
        "worker_id": event.get("hostname"),
        "queue": event.get("queue"),
        "correlation_id": event.get("correlation_id"),
        "trace_id": event.get("trace_id"),
        "payload": payload,
    }


def send(event):
    response = requests.post(
        f"{WAKER_URL}/api/connector/applications/{APPLICATION_ID}/events",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json=event,
        timeout=10,
    )
    if response.status_code == 409:
        # Stable event IDs make retry safe if the server already stored it.
        return
    response.raise_for_status()


def handle(event):
    event_type = event.get("type")
    normalized = normalize(event_type, event)
    if normalized:
        send(normalized)


def run():
    connection = app.connection_for_read()
    receiver = EventReceiver(connection, handlers={"*": handle})
    receiver.capture(limit=None, timeout=5, wakeup=True)


if __name__ == "__main__":
    while True:
        try:
            run()
        except (OperationalError, OSError, requests.RequestException) as exc:
            print(f"connector temporarily unavailable: {exc}", flush=True)
            time.sleep(2)
