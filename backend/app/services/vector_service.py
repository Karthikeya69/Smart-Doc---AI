import json
import boto3
import numpy as np
from app.config import get_settings
from app.services.bedrock_service import get_embedding
from app.text_utils import keywords

INDEX_KEY = "index/smartdoc_index.json"

_STATE = {
    "documents": {},
    "chunks": [],
}
_META = {"etag": None}


def _s3():
    cfg = get_settings()
    return boto3.client("s3", region_name=cfg["AWS_REGION"]), cfg["S3_BUCKET"]


def load_index_from_s3():
    client, bucket = _s3()
    try:
        obj = client.get_object(Bucket=bucket, Key=INDEX_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        _STATE["documents"] = data.get("documents", {})
        _STATE["chunks"] = data.get("chunks", [])
    except Exception:
        pass


def _refresh_if_changed():
    # Another pod may have updated the index in S3; reload only when the file changed.
    client, bucket = _s3()
    try:
        etag = client.head_object(Bucket=bucket, Key=INDEX_KEY)["ETag"]
    except Exception:
        return
    if etag != _META["etag"]:
        load_index_from_s3()
        _META["etag"] = etag


def save_index_to_s3():
    client, bucket = _s3()
    resp = client.put_object(
        Bucket=bucket,
        Key=INDEX_KEY,
        Body=json.dumps(_STATE).encode("utf-8"),
        ContentType="application/json",
    )
    _META["etag"] = resp["ETag"]


def get_documents(chat_id: str | None = None) -> list[dict]:
    _refresh_if_changed()
    docs = list(_STATE["documents"].values())
    if chat_id:
        return [d for d in docs if d.get("chat_id", "global") in ("global", chat_id)]
    return docs


def upsert_document_meta(doc_meta: dict):
    _refresh_if_changed()
    _STATE["documents"][doc_meta["name"]] = doc_meta
    save_index_to_s3()


def index_document_chunks(doc_name: str, chunks: list[dict]):
    _refresh_if_changed()
    _STATE["chunks"] = [c for c in _STATE["chunks"] if c["doc_name"] != doc_name]
    for c in chunks:
        c["embedding"] = get_embedding(c["text"])
        _STATE["chunks"].append(c)
    save_index_to_s3()


def delete_document(doc_name: str):
    _refresh_if_changed()
    _STATE["documents"].pop(doc_name, None)
    _STATE["chunks"] = [c for c in _STATE["chunks"] if c["doc_name"] != doc_name]
    save_index_to_s3()
    client, bucket = _s3()
    try:
        client.delete_object(Bucket=bucket, Key=f"pdfs/{doc_name}")
    except Exception:
        pass


def search_chunks(query: str, chat_id: str = "global", top_k: int = 4, min_score: float = 0.20) -> list[dict]:
    _refresh_if_changed()
    allowed = [c for c in _STATE["chunks"] if c.get("chat_id", "global") in ("global", chat_id)]
    q_words = keywords(query)
    if not allowed or not q_words:
        return []

    q_vec = np.array(get_embedding(query), dtype=np.float32)
    scored = []
    for c in allowed:
        overlap = len(q_words & keywords(c["text"]))
        if overlap == 0:
            continue  # no meaningful word in common: treat as not covered by the documents
        cos_sim = float(np.dot(q_vec, np.array(c["embedding"], dtype=np.float32)))
        score = cos_sim + 0.15 * overlap / len(q_words)
        if score >= min_score:
            scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "doc_name": c["doc_name"],
            "page_number": c["page_number"],
            "text": c["text"],
            "score": round(s, 3),
        }
        for s, c in scored[:top_k]
    ]