import csv
import io
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy import create_engine, select, text

from app.core.config import settings
from app.db.models import ProcessedRecord
from app.db.session import session_scope
from app.workers import workload_v1, workload_v2


def run_operation(name, payload, task_id, scope, correlation):
    cfg = settings()
    with httpx.Client(timeout=3) as client:
        response = client.get(
            cfg.dependency_url + "/internal/profile/" + scope, headers={"X-Control-Token": cfg.control_token}
        )
        response.raise_for_status()
        profile = response.json()
    if profile.get("delay"):
        time.sleep(min(float(profile["delay"]), 12))
    if profile.get("invalid_integer"):
        int("invalid_numeric_setting")
    if profile.get("exception"):
        raise ValueError("Record validation failed: unsupported field value")
    if profile.get("database_unavailable"):
        bad = create_engine(
            "postgresql+psycopg://unavailable:unavailable@127.0.0.1:1/unavailable",
            connect_args={"connect_timeout": 2},
        )
        try:
            with bad.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            bad.dispose()
    if name == "call_external_api":
        endpoint = (
            "http://127.0.0.1:1/work"
            if profile.get("connection_failure")
            else cfg.dependency_url + "/work/" + scope
        )
        with httpx.Client(timeout=2, follow_redirects=False) as client:
            response = client.get(endpoint, headers={"X-Correlation-ID": correlation})
            response.raise_for_status()
            return response.json()
    if name == "send_email":
        message = EmailMessage()
        message["From"] = "autopilot@example.test"
        message["To"] = payload["recipient"]
        message["Subject"] = "AutoPilot workload notification"
        message["Message-ID"] = f"<{task_id}@autopilot.local>"
        message.set_content("A background notification executed. Correlation: " + correlation)
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=5) as smtp:
            smtp.send_message(message)
        return {"message_id": str(message["Message-ID"]), "delivery": "accepted_by_smtp"}
    values = list(range(1, payload["count"] + 1))
    if name == "data_processing_task":
        return (workload_v2 if profile.get("revision") == "v2" else workload_v1).calculate(values)
    if name == "process_database_record":
        with session_scope() as db:
            existing = db.scalar(select(ProcessedRecord).where(ProcessedRecord.task_id == task_id))
            if existing:
                return existing.value
            value = {"sum": sum(values), "count": len(values)}
            db.add(ProcessedRecord(task_id=task_id, value=value))
            return value
    directory = Path(cfg.artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if name == "process_report":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["record", "square"])
        writer.writerows((v, v * v) for v in values)
        target = directory / (task_id + ".csv")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(buffer.getvalue(), encoding="utf-8")
        temporary.replace(target)
        return {"artifact": target.name, "records": len(values)}
    if name == "resize_image":
        size = payload["size"]
        picture = Image.new("RGB", (1024, 1024), color=(25, 105, 230)).resize(
            (size, size), Image.Resampling.LANCZOS
        )
        target = directory / (task_id + ".png")
        temporary = target.with_suffix(".tmp")
        picture.save(temporary, format="PNG")
        temporary.replace(target)
        return {"artifact": target.name, "width": size, "height": size}
    raise ValueError("Unknown operation")
