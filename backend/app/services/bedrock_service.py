import hashlib
import json
import os
import re
import urllib.request
import boto3
import numpy as np
from app.config import get_settings


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
    try:
        client, cfg = _bedrock_client()
        body = json.dumps({
            "inputText": text[:8000],
            "dimensions": 512,
            "normalize": True,
        })
        resp = client.invoke_model(
            modelId=cfg["EMBED_MODEL"],
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(resp["body"].read())["embedding"]
    except Exception:
        return _local_embedding(text)


def _call_gemini(prompt: str, system_prompt: str, api_key: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 700},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _extractive_fallback(question: str, chunks: list[dict]) -> str:
    q_words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
    scored = []
    for c in chunks:
        for s in re.split(r"(?<=[.!?])\s+", c["text"]):
            s_clean = s.strip()
            if len(s_clean) < 25 or not s_clean[0].isupper():
                continue
            overlap = len(q_words & set(re.findall(r"[a-z0-9]+", s_clean.lower())))
            if overlap > 0:
                scored.append((overlap, s_clean, c["doc_name"], c["page_number"]))

    if not scored:
        return "Information not available in the uploaded documents."

    scored.sort(key=lambda x: x[0], reverse=True)
    bullets = []
    seen = set()
    for _, sent, doc, page in scored[:4]:
        if sent not in seen:
            seen.add(sent)
            bullets.append(f"• {sent} ({doc}, Page {page})")
    return "Based on your uploaded documents:\n\n" + "\n".join(bullets)


def generate_rag_answer(question: str, chunks: list[dict], history: list[dict] | None = None) -> str:
    if not chunks:
        return "Information not available in the uploaded documents."

    context_text = "\n\n".join(
        f"[Source {i} | Document: {c['doc_name']} | Page: {c['page_number']}]\n{c['text']}"
        for i, c in enumerate(chunks, start=1)
    )

    system_prompt = (
        "You are SmartDoc AI, a helpful enterprise document assistant. "
        "Synthesize a clear, natural, well-structured answer using ONLY the provided Source Passages. "
        "Cite the document name and page number inline (e.g., (assignment.pdf, Page 3)). "
        "If the answer is not supported by the Source Passages, reply ONLY with: "
        "\"Information not available in the uploaded documents.\""
    )
    user_prompt = f"Source Passages:\n{context_text}\n\nQuestion: {question}"

    # 1. Try Amazon Bedrock
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
    except Exception:
        pass

    # 2. Try Gemini API if GEMINI_API_KEY is configured
    gemini_key = os.getenv("GEMINI_API_KEY") or get_settings().get("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            return _call_gemini(user_prompt, system_prompt, gemini_key)
        except Exception:
            pass

    # 3. Clean bulleted extractive fallback
    return _extractive_fallback(question, chunks)