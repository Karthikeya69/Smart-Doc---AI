import hashlib
import json
import re
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
        payload = json.loads(resp["body"].read())
        return payload["embedding"]
    except Exception as e:
        print(f"[Bedrock Fallback] Using local vector embedding ({e})")
        return _local_embedding(text)


def _extractive_fallback(question: str, chunks: list[dict]) -> str:
    q_words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
    scored_sents = []
    for c in chunks:
        sents = re.split(r"(?<=[.!?])\s+", c["text"])
        for s in sents:
            s_words = set(re.findall(r"[a-z0-9]+", s.lower()))
            overlap = len(q_words & s_words)
            if overlap > 0 and len(s.strip()) > 15:
                scored_sents.append((overlap, s.strip(), c["doc_name"], c["page_number"]))

    if not scored_sents:
        return "Information not available in the uploaded documents."

    scored_sents.sort(key=lambda x: x[0], reverse=True)
    lines = []
    seen = set()
    for _, sent, doc, page in scored_sents[:3]:
        if sent not in seen:
            seen.add(sent)
            lines.append(f"{sent} ({doc}, Page {page})")
    return "\n\n".join(lines)


def generate_rag_answer(question: str, chunks: list[dict], history: list[dict] | None = None) -> str:
    if not chunks:
        return "Information not available in the uploaded documents."

    try:
        client, cfg = _bedrock_client()
        context_blocks = [
            f"[Source {i} | Document: {c['doc_name']} | Page: {c['page_number']}]\n{c['text']}"
            for i, c in enumerate(chunks, start=1)
        ]
        context_text = "\n\n".join(context_blocks)

        system_prompt = (
            "You are SmartDoc AI, an enterprise document assistant. "
            "Answer the user's question using ONLY the provided Source Passages below. "
            "Cite the document name and page number inline (e.g., (assignment.pdf, Page 3)). "
            "If the answer is not explicitly supported by the Source Passages, reply ONLY with: "
            "\"Information not available in the uploaded documents.\" "
            "Do not fabricate or use outside knowledge."
        )

        messages = []
        if history:
            for turn in history[-4:]:
                role = "user" if turn.get("role") == "user" else "assistant"
                content = turn.get("content", "").strip()
                if content:
                    messages.append({"role": role, "content": [{"text": content}]})

        messages.append({"role": "user", "content": [{"text": f"Source Passages:\n{context_text}\n\nQuestion: {question}"}]})

        resp = client.converse(
            modelId=cfg["CHAT_MODEL"],
            system=[{"text": system_prompt}],
            messages=messages,
            inferenceConfig={"temperature": 0.1, "maxTokens": 600},
        )
        return resp["output"]["message"]["content"][0]["text"].strip()
    except Exception as e:
        print(f"[Bedrock Fallback] Using extractive grounded answer ({e})")
        return _extractive_fallback(question, chunks)