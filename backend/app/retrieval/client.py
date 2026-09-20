import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.safety import redact
from app.db.models import HistoricalIncident, Verification


def search_history(incident, cutoff, allowed_ids=None):
    cfg = settings()
    if cfg.embedding_provider == "disabled":
        return {"available": False, "reason": "Historical retrieval disabled", "matches": []}
    try:
        with httpx.Client(timeout=cfg.tool_timeout_seconds) as client:
            response = client.post(
                cfg.embedding_service_url + "/search",
                headers={"X-Control-Token": cfg.control_token},
                json={
                    "query": incident.title + " " + str(incident.symptoms),
                    "limit": cfg.rag_max_results,
                    "cutoff": cutoff.timestamp(),
                    "allowed_ids": allowed_ids,
                    "exclude_incident": incident.id,
                },
            )
            response.raise_for_status()
            data = response.json()
            return {
                "available": True,
                "matches": [
                    {
                        "id": r["id"],
                        "incident_id": r["incident_id"],
                        "document": redact(r["document"]),
                        "distance": r["distance"],
                    }
                    for r in data["matches"][: cfg.rag_max_results]
                ],
            }
    except (httpx.HTTPError, ValueError, KeyError):
        return {
            "available": False,
            "reason": "Historical retrieval unavailable; investigation can continue",
            "matches": [],
        }


def remember_recovered(db, incident, diagnosis):
    verified = db.scalar(
        select(Verification).where(Verification.diagnosis_id == diagnosis.id)
        .order_by(Verification.created_at.desc()).limit(1)
    )
    if (
        incident.status != "RECOVERED"
        or not verified or not verified.verified
        or db.scalar(select(HistoricalIncident).where(HistoricalIncident.incident_id == incident.id))
    ):
        return
    document = redact(
        f"Symptoms: {incident.symptoms}\nDiagnosis: {diagnosis.root_cause}: {diagnosis.summary}\nComponent: {diagnosis.affected_component}\nAction: {diagnosis.recommended_action}\nOutcome: RECOVERED; diagnosis verified against operational evidence."
    )
    db.add(
        HistoricalIncident(
            incident_id=incident.id,
            diagnosis_id=diagnosis.id,
            document=document,
            outcome="RECOVERED",
            embedding_model=settings().embedding_model,
        )
    )


def index_pending(db):
    cfg = settings()
    if cfg.embedding_provider == "disabled":
        return
    for record in db.scalars(
        select(HistoricalIncident).where(HistoricalIncident.indexed.is_(False)).limit(5)
    ):
        try:
            with httpx.Client(timeout=8) as client:
                response = client.post(
                    cfg.embedding_service_url + "/index",
                    headers={"X-Control-Token": cfg.control_token},
                    json={
                        "id": record.id,
                        "incident_id": record.incident_id,
                        "document": record.document,
                        "created_epoch": record.created_at.timestamp(),
                    },
                )
                response.raise_for_status()
            record.indexed = True
            record.index_error = None
        except httpx.HTTPError:
            record.index_error = "Embedding service unavailable; indexing will retry"
