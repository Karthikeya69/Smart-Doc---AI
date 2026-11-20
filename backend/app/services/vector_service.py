import difflib
import json
import os

import boto3
import numpy as np

from app.config import get_settings
from app.services.embeddings import HASH_TAG, embed_documents, embed_query, local_embedding
from app.text_utils import keywords

INDEX_KEY = "index/smartdoc_index.json"

# Semantic scoring (Gemini vectors). Keyword matching stays the guard against off-topic questions:
# a chunk needs a shared keyword, or a very high semantic similarity (SEM_RESCUE), to be considered.
SEM_MIN_COS = float(os.getenv("SEM_MIN_COS", "0.30"))
SEM_RESCUE = float(os.getenv("SEM_RESCUE", "0.72"))

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
    # One batched call for the whole document. Each chunk remembers which model made its vector.
    vectors, tag = embed_documents([c["text"] for c in chunks])
    for c, vec in zip(chunks, vectors):
        c["embedding"] = vec
        c["emb"] = tag
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


def get_doc_chunks(doc_names: list[str], chat_id: str = "global") -> list[dict]:
    """All chunks of the given documents, in page order (used for summaries)."""
    _refresh_if_changed()
    names = set(doc_names)
    return [
        c for c in _STATE["chunks"]
        if c["doc_name"] in names and c.get("chat_id", "global") in ("global", chat_id)
    ]


def _overlap(q_words: set[str], chunk_words: set[str]) -> int:
    """How many question words appear in the chunk. Close spellings count (typos, plurals)."""
    hits = 0
    pool = None
    for w in q_words:
        if w in chunk_words:
            hits += 1
        elif len(w) >= 5:
            if pool is None:
                pool = list(chunk_words)
            if difflib.get_close_matches(w, pool, n=1, cutoff=0.86):
                hits += 1
    return hits


def search_chunks(query: str, chat_id: str = "global", top_k: int = 4, min_score: float = 0.20) -> list[dict]:
    _refresh_if_changed()
    allowed = [c for c in _STATE["chunks"] if c.get("chat_id", "global") in ("global", chat_id)]
    q_words = keywords(query)
    if not allowed or not q_words:
        return []

    q_local = np.array(local_embedding(query), dtype=np.float32)
    remote = embed_query(query)  # (vector, tag) from Gemini, or None
    q_remote, q_tag = remote if remote else (None, None)

    scored = []
    for c in allowed:
        overlap = _overlap(q_words, keywords(c["text"]))
        ratio = overlap / len(q_words)
        tag = c.get("emb", HASH_TAG)  # chunks indexed before tagging were hash vectors
        vec = np.asarray(c["embedding"], dtype=np.float32)

        cos, semantic = None, False
        if tag == HASH_TAG and vec.shape == q_local.shape:
            cos = float(np.dot(q_local, vec))
        elif q_remote is not None and tag == q_tag and vec.shape == q_remote.shape:
            cos, semantic = float(np.dot(q_remote, vec)), True

        if semantic:
            if overlap == 0 and cos < SEM_RESCUE:
                continue  # no shared word and not clearly the same meaning: not covered
            if cos < SEM_MIN_COS:
                continue
            score = cos + 0.15 * ratio
        elif cos is None:
            # Chunk has a semantic vector but the query could not be embedded right now: keywords only.
            if overlap == 0:
                continue
            score = 0.3 + 0.5 * ratio
        else:
            if overlap == 0:
                continue  # no meaningful word in common: treat as not covered by the documents
            score = cos + 0.15 * ratio
            if score < min_score:
                continue
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
