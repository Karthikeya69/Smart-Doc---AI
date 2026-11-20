"""Embeddings for SmartDoc AI: semantic vectors from the Gemini embedding model.

Every vector carries a TAG naming the model that produced it. Vectors with different tags are never
compared with each other, so old word-hash vectors and new semantic vectors can safely live in the same
index. If Gemini cannot be reached, a local hashed bag-of-words vector (tag "hash") is used instead, so
uploads and searches keep working, only with weaker matching.

Why Gemini: Amazon Bedrock (Titan embeddings) is blocked on this AWS account, and the Gemini API key is
already used for answer generation, so no extra service or credential is needed.
"""
import os
import time
import urllib.error

import numpy as np

from app.config import get_settings
from app.services import bedrock_service as bs

HASH_TAG = "hash"
EMBED_DIMS = int(os.getenv("EMBED_DIMS", "768"))
BATCH_SIZE = 50  # the API accepts up to 100 texts per batch request
_STATE = {"model": None, "down_until": 0.0, "dims_ok": True}
_TRANSIENT = {429, 500, 502, 503, 504}


def local_embedding(text: str) -> list[float]:
    """Hashed bag-of-words vector (512 dims). Used when Gemini is unreachable."""
    return bs._local_embedding(text)


def _api_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or get_settings().get("GEMINI_API_KEY", "") or "").strip()


def _available() -> bool:
    return time.time() >= _STATE["down_until"]


def _mark_down(err, seconds: int = 60) -> None:
    # After a failure, skip Gemini embeddings for a short while so requests are not slowed down.
    _STATE["down_until"] = time.time() + seconds
    print(f"[Embeddings] Gemini unavailable for {seconds}s: {str(err)[:160]}", flush=True)


def _pick_model(key: str) -> str:
    if _STATE["model"]:
        return _STATE["model"]
    chosen = None
    try:
        data = bs._gemini_http(f"{bs.GEMINI_BASE}/models?pageSize=1000", key, timeout=15)
        names = [
            m["name"] for m in data.get("models", [])
            if "embedContent" in m.get("supportedGenerationMethods", [])
        ]
        preferred = [n for n in names if n.endswith("gemini-embedding-001")]
        chosen = (preferred or sorted(names, reverse=True) or [None])[0]
    except Exception as e:
        print(f"[Embeddings] model list failed: {str(e)[:160]}", flush=True)
    _STATE["model"] = chosen or "models/gemini-embedding-001"
    print(f"[Embeddings] using {_STATE['model']}", flush=True)
    return _STATE["model"]


def _build_request(model: str, text: str, task: str) -> dict:
    req = {
        "model": model,
        "content": {"parts": [{"text": (text or " ")[:8000]}]},
        "taskType": task,
    }
    if _STATE["dims_ok"]:
        req["outputDimensionality"] = EMBED_DIMS
    return req


def _post(path: str, build, key: str, timeout: int) -> dict:
    """POST to the Gemini API. If the model rejects outputDimensionality, retry once without it."""
    for attempt in range(2):
        try:
            return bs._gemini_http(f"{bs.GEMINI_BASE}/{path}", key, build(), timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:300]
            if e.code == 400 and _STATE["dims_ok"] and "dimension" in body.lower() and attempt == 0:
                _STATE["dims_ok"] = False
                continue
            print(f"[Embeddings HTTP {e.code}] {body}", flush=True)
            raise


def _normalize(values) -> np.ndarray:
    a = np.asarray(values, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


def _tag(model: str, dims: int) -> str:
    return f"gemini:{model.split('/')[-1]}:{dims}"


def _embed_batch(key: str, model: str, texts: list[str]) -> list[list[float]]:
    def build():
        return {"requests": [_build_request(model, t, "RETRIEVAL_DOCUMENT") for t in texts]}

    data = _post(f"{model}:batchEmbedContents", build, key, timeout=40)
    embs = data["embeddings"]
    if len(embs) != len(texts):
        raise RuntimeError("embedding count mismatch")
    return [_normalize(e["values"]).round(5).tolist() for e in embs]


def _embed_batch_retry(key: str, model: str, texts: list[str]) -> list[list[float]]:
    for attempt in range(2):
        try:
            return _embed_batch(key, model, texts)
        except urllib.error.HTTPError as e:
            if e.code in _TRANSIENT and attempt == 0:
                time.sleep(1.5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 0:
                time.sleep(1.5)
                continue
            raise


def embed_documents(texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed document chunks. Returns (vectors, tag). Falls back to local hash vectors if Gemini fails."""
    if not texts:
        return [], HASH_TAG
    key = _api_key()
    if key and _available():
        try:
            model = _pick_model(key)
            vectors: list[list[float]] = []
            for i in range(0, len(texts), BATCH_SIZE):
                vectors += _embed_batch_retry(key, model, texts[i:i + BATCH_SIZE])
            tag = _tag(model, len(vectors[0]))
            print(f"[Embeddings OK] {len(vectors)} chunk(s) embedded with {tag}", flush=True)
            return vectors, tag
        except Exception as e:
            _mark_down(e)
    print("[Embeddings] using local hashed vectors for this document", flush=True)
    return [local_embedding(t) for t in texts], HASH_TAG


def embed_query(text: str):
    """Returns (unit vector, tag) from Gemini, or None when unavailable (callers then use local vectors)."""
    key = _api_key()
    if not key or not _available():
        return None
    try:
        model = _pick_model(key)

        def build():
            return _build_request(model, text, "RETRIEVAL_QUERY")

        data = _post(f"{model}:embedContent", build, key, timeout=12)
        vec = _normalize(data["embedding"]["values"])
        return vec, _tag(model, len(vec))
    except Exception as e:
        _mark_down(e, seconds=30)
        return None
