import hmac
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

collection = None
model = None
initialization_error = None
MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


def initialize():
    global collection, model, initialization_error
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL, cache_folder="/models", device="cpu", trust_remote_code=False)
        client = chromadb.PersistentClient(path="/data/chroma")
        collection = client.get_or_create_collection(
            "recovered_incidents", metadata={"hnsw:space": "cosine", "embedding_model": MODEL}
        )
        if collection.metadata.get("embedding_model") != MODEL:
            raise ValueError("Embedding model changed; use a new Chroma volume and reindex")
    except Exception as exc:
        initialization_error = type(exc).__name__
        collection = None


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=initialize, daemon=True).start()
    yield


app = FastAPI(title="Local historical incident retrieval", lifespan=lifespan)


def internal(x_control_token: str = Header(default="")):
    path = Path(os.getenv("INTERNAL_TOKEN_FILE", "/run/autopilot/control_token"))
    expected = os.getenv("INTERNAL_TOKEN", "") or (path.read_text().strip() if path.exists() else "")
    if not expected or not hmac.compare_digest(expected, x_control_token):
        raise HTTPException(403, "Control authorization required")


def ready():
    if collection is None:
        raise HTTPException(503, "Local embedding model is loading or unavailable")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(Strict):
    id: str = Field(max_length=36)
    incident_id: str = Field(max_length=36)
    document: str = Field(max_length=12000)
    created_epoch: float


class Search(Strict):
    query: str = Field(max_length=12000)
    limit: int = Field(4, ge=1, le=10)
    cutoff: float
    allowed_ids: list[str] | None = Field(None, max_length=100000)
    exclude_incident: str = Field(max_length=36)


@app.get("/health")
def health():
    if collection is None:
        raise HTTPException(503, initialization_error or "Downloading or loading local embedding model")
    return {"status": "HEALTHY", "model": MODEL, "documents": collection.count()}


@app.post("/index", dependencies=[Depends(internal), Depends(ready)])
def index(body: Document):
    vector = model.encode([body.document], normalize_embeddings=True).tolist()
    collection.upsert(
        ids=[body.id],
        documents=[body.document],
        embeddings=vector,
        metadatas=[
            {"incident_id": body.incident_id, "created_epoch": body.created_epoch, "record_id": body.id}
        ],
    )
    return {"indexed": True}


@app.post("/search", dependencies=[Depends(internal), Depends(ready)])
def search(body: Search):
    if body.allowed_ids == [] or collection.count() == 0:
        return {"matches": []}
    clauses = [{"created_epoch": {"$lt": body.cutoff}}, {"incident_id": {"$ne": body.exclude_incident}}]
    if body.allowed_ids is not None:
        clauses.append({"record_id": {"$in": body.allowed_ids}})
    vectors = model.encode([body.query], normalize_embeddings=True).tolist()
    data = collection.query(
        query_embeddings=vectors,
        n_results=min(body.limit, collection.count()),
        where={"$and": clauses},
        include=["documents", "metadatas", "distances"],
    )
    return {
        "matches": [
            {"id": identifier, "incident_id": meta["incident_id"], "document": doc, "distance": distance}
            for identifier, meta, doc, distance in zip(
                data["ids"][0], data["metadatas"][0], data["documents"][0], data["distances"][0], strict=True
            )
        ]
    }
