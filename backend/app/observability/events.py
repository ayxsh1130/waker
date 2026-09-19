from app.core.safety import redact
from app.db.models import IncidentEvent, StreamEvent, TaskLog


def emit(db, topic, entity_id, data=None):
    db.add(StreamEvent(topic=topic, entity_id=entity_id, data=redact(data or {})))


def incident_event(db, incident_id, kind, content):
    db.add(IncidentEvent(incident_id=incident_id, kind=kind, content=redact(content)))
    emit(db, "incident", incident_id, {"stage": kind})


def task_log(db, task_id, level, message, data=None):
    db.add(TaskLog(task_id=task_id, level=level, message=redact(message), data=redact(data or {})))
    emit(db, "task", task_id, {"level": level})
