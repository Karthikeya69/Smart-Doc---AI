import json
import re
import boto3
import numpy as np
from app.config import get_settings
from app.services.bedrock_service import get_embedding

INDEX_KEY = "index/smartdoc_index.json"

_STATE = {
    "documents": {},
    "chunks": [],
}


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


def save_index_to_s3():
    client, bucket = _s3()
    client.put_object(
        Bucket=bucket,
        Key=INDEX_KEY,
        Body=json.dumps(_STATE).encode("utf-8"),
        ContentType="application/json",
    )


def get_documents(chat_id: str | None = None) -> list[dict]:
    load_index_from_s3()
    docs = list(_STATE["documents"].values())
    if chat_id:
        return [d for d in docs if d.get("chat_id", "global") in ("global", chat_id)]
    return docs


def upsert_document_meta(doc_meta: dict):
    load_index_from_s3()
    _STATE["documents"][doc_meta["name"]] = doc_meta
    save_index_to_s3()


def index_document_chunks(doc_name: str, chunks: list[dict]):
    load_index_from_s3()
    _STATE["chunks"] = [c for c in _STATE["chunks"] if c["doc_name"] != doc_name]
    for c in chunks:
        c["embedding"] = get_embedding(c["text"])
        _STATE["chunks"].append(c)
    save_index_to_s3()


def delete_document(doc_name: str):
    load_index_from_s3()
    _STATE["documents"].pop(doc_name, None)
    _STATE["chunks"] = [c for c in _STATE["chunks"] if c["doc_name"] != doc_name]
    save_index_to_s3()
    client, bucket = _s3()
    try:
        client.delete_object(Bucket=bucket, Key=f"pdfs/{doc_name}")
    except Exception:
        pass


def search_chunks(query: str, chat_id: str = "global", top_k: int = 5, min_score: float = 0.08) -> list[dict]:
    load_index_from_s3()
    allowed = [c for c in _STATE["chunks"] if c.get("chat_id", "global") in ("global", chat_id)]
    if not allowed:
        return []

    q_vec = np.array(get_embedding(query), dtype=np.float32)
    q_words = set(re.findall(r"\w+", query.lower()))

    scored = []
    for c in allowed:
        c_vec = np.array(c["embedding"], dtype=np.float32)
        cos_sim = float(np.dot(q_vec, c_vec))
        c_words = set(re.findall(r"\w+", c["text"].lower()))
        kw_overlap = len(q_words & c_words) / max(len(q_words), 1)
        hybrid_score = cos_sim + 0.20 * kw_overlap
        if hybrid_score >= min_score:
            scored.append((hybrid_score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "doc_name": c["doc_name"],
            "page_number": c["page_number"],
            "text": c["text"],
            "score": round(score, 3),
        }
        for score, c in scored[:top_k]
    ]