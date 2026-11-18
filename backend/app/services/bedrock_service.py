from app.text_utils import keywords
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import boto3
import numpy as np
from app.config import get_settings

NOT_FOUND = "Information not available in the uploaded documents."

_BEDROCK = {"down_until": 0.0}


def _bedrock_available() -> bool:
    return time.time() >= _BEDROCK["down_until"]


def _bedrock_mark_down(err) -> None:
    # After a failure, skip Bedrock for 5 minutes so requests are not slowed down.
    _BEDROCK["down_until"] = time.time() + 300
    print(f"[Bedrock unavailable for 5 min] {str(err)[:160]}", flush=True)


def _bedrock_client():
    cfg = get_settings()
    return boto3.client("bedrock-runtime", region_name=cfg["AWS_REGION"]), cfg


def _local_embedding(text: str, dims: int = 512) -> list[float]:
    vec = np.zeros(dims, dtype=np.float32)
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for tok in tokens:
        idx = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % dims
        vec[idx] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def get_embedding(text: str) -> list[float]:
    if _bedrock_available():
        try:
            client, cfg = _bedrock_client()
            body = json.dumps({"inputText": text[:8000], "dimensions": 512, "normalize": True})
            resp = client.invoke_model(
                modelId=cfg["EMBED_MODEL"],
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(resp["body"].read())["embedding"]
        except Exception as e:
            _bedrock_mark_down(e)
    return _local_embedding(text)


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI = {"order": None, "good": None}


def _gemini_http(url: str, api_key: str, payload: dict | None = None, timeout: int = 25) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _gemini_model_order(api_key: str) -> list[str]:
    if _GEMINI["order"]:
        return _GEMINI["order"]
    order = ["models/gemini-flash-latest"]
    try:
        models = _gemini_http(f"{GEMINI_BASE}/models", api_key, timeout=15).get("models", [])
        skip = ("lite", "image", "tts", "live", "audio", "embedding", "exp", "vision", "robotics", "computer")
        flash = [
            m["name"] for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
            and "flash" in m["name"].lower()
            and not any(s in m["name"].lower() for s in skip)
        ]
        stable = sorted((n for n in flash if "preview" not in n.lower()), reverse=True)
        preview = sorted((n for n in flash if "preview" in n.lower()), reverse=True)
        order += stable + preview
    except Exception as e:
        print(f"[Gemini model list failed] {str(e)[:160]}", flush=True)
        order.append("models/gemini-2.5-flash")
    seen = set()
    _GEMINI["order"] = [n for n in order if not (n in seen or seen.add(n))]
    return _GEMINI["order"]


def _call_gemini(prompt: str, system_prompt: str, api_key: str) -> str:
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000},
    }
    order = _gemini_model_order(api_key)
    if _GEMINI["good"] in order:
        order = [_GEMINI["good"]] + [n for n in order if n != _GEMINI["good"]]
    last_err = None
    for model in order[:5]:
        try:
            data = _gemini_http(f"{GEMINI_BASE}/{model}:generateContent", api_key, payload)
            cand = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
            if not text:
                print(f"[Gemini empty answer] {model} finishReason={cand.get('finishReason')}", flush=True)
                continue
            _GEMINI["good"] = model
            print(f"[Gemini OK] {model}", flush=True)
            return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:300]
            print(f"[Gemini HTTP {e.code}] {model}: {body}", flush=True)
            last_err = e
            if e.code == 404:
                continue  # this model name is not available; try the next one
            raise
    raise last_err or RuntimeError("No Gemini model returned an answer")


def _extractive_fallback(question: str, chunks: list[dict]) -> str:
    q = keywords(question)
    if not q:
        return NOT_FOUND
    scored = []
    for c in chunks:
        for s in re.split(r"(?<=[.!?])\s+", c["text"]):
            s = s.strip()
            if len(s) < 25 or s.endswith("?"):
                continue
            hits = len(q & keywords(s))
            if hits:
                scored.append((hits, s, c["doc_name"], c["page_number"]))
    if not scored:
        return NOT_FOUND
    scored.sort(key=lambda x: -x[0])
    body = "\n\n".join(f"- {s} ({d}, Page {p})" for _, s, d, p in scored[:3])
    return "Note: the AI model is unavailable, so these are the closest passages.\n\n" + body


def generate_rag_answer(question: str, chunks: list[dict], history: list[dict] | None = None, available_docs: list[str] | None = None) -> str:
    if not chunks:
        return NOT_FOUND

    context_text = "\n\n".join(
        f"[Source {i} | Document: {c['doc_name']} | Page: {c['page_number']}]\n{c['text']}"
        for i, c in enumerate(chunks, start=1)
    )

    doc_list = ", ".join(available_docs) if available_docs else "none"
    system_prompt = (
        f"You are SmartDoc AI, an enterprise document assistant. Available documents: {doc_list}. "
        "If the user asks a broad question across multiple unrelated PDFs, briefly clarify which document they want or organize by document name. "
        "Write concise, cleanly structured answers: start with a 1-sentence summary, then list neatly mapped bullet points (Key: Value). "
        "Use ONLY the provided Source Passages and cite (doc_name, Page N) inline. "
        "If the answer is not in the Source Passages, reply ONLY with: "
        "\"Information not available in the uploaded documents.\""
    )
    user_prompt = f"Source Passages:\n{context_text}\n\nQuestion: {question}"

    if _bedrock_available():
        try:
            client, cfg = _bedrock_client()
            messages = []
            if history:
                for turn in history[-4:]:
                    role = "user" if turn.get("role") == "user" else "assistant"
                    content = turn.get("content", "").strip()
                    if content:
                        messages.append({"role": role, "content": [{"text": content}]})
            messages.append({"role": "user", "content": [{"text": user_prompt}]})
            resp = client.converse(
                modelId=cfg["CHAT_MODEL"],
                system=[{"text": system_prompt}],
                messages=messages,
                inferenceConfig={"temperature": 0.1, "maxTokens": 600},
            )
            return resp["output"]["message"]["content"][0]["text"].strip()
        except Exception as e:
            _bedrock_mark_down(e)

    gemini_key = os.getenv("GEMINI_API_KEY") or get_settings().get("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            return _call_gemini(user_prompt, system_prompt, gemini_key.strip())
        except Exception as e:
            print(f"[Gemini Fallback Triggered] {str(e)[:200]}", flush=True)

    return _extractive_fallback(question, chunks)