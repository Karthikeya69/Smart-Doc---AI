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
        print(f"[VectorStore] Loaded {len(_STATE['chunks'])} chunks from S3.")
    except client.exceptions.NoSuchKey:
        print("[VectorStore] No existing index in S3 yet; starting fresh.")
    except Exception as e:
        print(f"[VectorStore] Load skipped: {e}")


def save_index_to_s3():
    client, bucket = _s3()
    client.put_object(
        Bucket=bucket,
        Key=INDEX_KEY,
        Body=json.dumps(_STATE).encode("utf-8"),
        ContentType="application/json",
    )


def get_documents() -> list[dict]:
    if not _STATE["documents"]:
        load_index_from_s3()
    return list(_STATE["documents"].values())


def upsert_document_meta(doc_meta: dict):
    _STATE["documents"][doc_meta["name"]] = doc_meta
    save_index_to_s3()


def index_document_chunks(doc_name: str, chunks: list[dict]):
    # Remove old chunks for same filename if re-uploaded
    _STATE["chunks"] = [c for c in _STATE["chunks"] if c["doc_name"] != doc_name]
    for c in chunks:
        c["embedding"] = get_embedding(c["text"])
        _STATE["chunks"].append(c)
    save_index_to_s3()


def search_chunks(query: str, top_k: int = 4, min_score: float = 0.22) -> list[dict]:
    if not _STATE["chunks"]:
        load_index_from_s3()
    if not _STATE["chunks"]:
        return []

    q_vec = np.array(get_embedding(query), dtype=np.float32)
    q_words = set(re.findall(r"\w+", query.lower()))

    scored = []
    for c in _STATE["chunks"]:
        c_vec = np.array(c["embedding"], dtype=np.float32)
        cos_sim = float(np.dot(q_vec, c_vec))
        c_words = set(re.findall(r"\w+", c["text"].lower()))
        kw_overlap = len(q_words & c_words) / max(len(q_words), 1)
        hybrid_score = cos_sim + 0.15 * kw_overlap
        if hybrid_score >= min_score:
            scored.append((hybrid_score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, c in scored[:top_k]:
        results.append({
            "doc_name": c["doc_name"],
            "page_number": c["page_number"],
            "text": c["text"],
            "score": round(score, 3),
        })
    return results