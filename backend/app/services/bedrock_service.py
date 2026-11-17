import json
import boto3
from app.config import get_settings


def _bedrock_client():
    cfg = get_settings()
    return boto3.client("bedrock-runtime", region_name=cfg["AWS_REGION"]), cfg


def get_embedding(text: str) -> list[float]:
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


def generate_rag_answer(question: str, chunks: list[dict], history: list[dict] | None = None) -> str:
    if not chunks:
        return "Information not available in the uploaded documents."

    client, cfg = _bedrock_client()

    context_blocks = []
    for i, c in enumerate(chunks, start=1):
        context_blocks.append(
            f"[Source {i} | Document: {c['doc_name']} | Page: {c['page_number']}]\n{c['text']}"
        )
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

    user_prompt = f"Source Passages:\n{context_text}\n\nQuestion: {question}"
    messages.append({"role": "user", "content": [{"text": user_prompt}]})

    resp = client.converse(
        modelId=cfg["CHAT_MODEL"],
        system=[{"text": system_prompt}],
        messages=messages,
        inferenceConfig={"temperature": 0.1, "maxTokens": 600},
    )
    return resp["output"]["message"]["content"][0]["text"].strip()