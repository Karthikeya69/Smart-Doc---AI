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
_GEMINI = {"order": None, "good": None, "cool_until": 0.0}


def gemini_cooling() -> bool:
    """True while we are backing off after a quota (429) error."""
    return time.time() < _GEMINI["cool_until"]


def gemini_cool_down(body: str) -> None:
    # Daily quota: back off long. Per-minute quota: back off briefly. Retrying only burns more quota.
    seconds = 900 if "perday" in body.lower().replace(" ", "").replace("_", "") else 60
    _GEMINI["cool_until"] = time.time() + seconds
    print(f"[Gemini quota exceeded] pausing Gemini calls for {seconds}s", flush=True)


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


_TRANSIENT_CODES = {429, 500, 502, 503, 504}


def _call_gemini(prompt: str, system_prompt: str, api_key: str) -> str:
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000,
                             "thinkingConfig": {"thinkingBudget": 0}},  # no hidden reasoning: much faster
    }
    if gemini_cooling():
        raise RuntimeError("Gemini paused after quota error")
    order = _gemini_model_order(api_key)
    if _GEMINI["good"] in order:
        order = [_GEMINI["good"]] + [n for n in order if n != _GEMINI["good"]]

    deadline = time.time() + 40
    last_err = None
    for model in order[:4]:
        for attempt in range(2):
            if time.time() > deadline:
                break
            try:
                data = _gemini_http(f"{GEMINI_BASE}/{model}:generateContent", api_key, payload)
                cand = (data.get("candidates") or [{}])[0]
                text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
                if not text:
                    print(f"[Gemini empty answer] {model} finishReason={cand.get('finishReason')}", flush=True)
                    break  # try the next model
                _GEMINI["good"] = model
                print(f"[Gemini OK] {model} (attempt {attempt + 1})", flush=True)
                return text
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:900]
                print(f"[Gemini HTTP {e.code}] {model} attempt {attempt + 1}: {body}", flush=True)
                last_err = e
                if e.code == 429:
                    gemini_cool_down(body)
                    raise  # same key and project: other models will fail too, so stop here
                if e.code == 400 and "thinking" in body.lower() and "thinkingConfig" in payload["generationConfig"]:
                    del payload["generationConfig"]["thinkingConfig"]  # this model rejects it: retry without
                    continue
                if e.code in _TRANSIENT_CODES:
                    if attempt == 0:
                        time.sleep(1.5)
                        continue  # one retry on the same model
                    break  # then try the next model
                if e.code == 404:
                    break  # model name not available
                raise  # real error (bad key, bad request): stop and show it
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"[Gemini network error] {model} attempt {attempt + 1}: {str(e)[:150]}", flush=True)
                last_err = e
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                break
    raise last_err or RuntimeError("No Gemini model returned an answer")


def _best_window(text: str, q: set[str], width: int = 240) -> tuple[int, str]:
    """The short stretch of text with the most question words (OCR text often has no sentence breaks)."""
    text = " ".join(text.split())
    words = [(m.start(), m.group().lower()) for m in re.finditer(r"[A-Za-z0-9]+", text)]
    hits = [(pos, w) for pos, w in words if w in q]
    if not hits:
        return 0, ""
    best = (0, 0)
    for pos, _ in hits:
        start = max(0, pos - 70)
        seen = {w for p, w in hits if start <= p < start + width}
        if len(seen) > best[0]:
            best = (len(seen), start)
    n, start = best
    end = min(len(text), start + width)
    if start > 0:
        start = text.rfind(" ", 0, start) + 1  # do not cut a word in half
    if end < len(text):
        cut = text.rfind(" ", start, end)
        end = cut if cut > start else end
    snippet = text[start:end].strip()
    return n, ("... " if start > 0 else "") + snippet + (" ..." if end < len(text) else "")


def _extractive_fallback(question: str, chunks: list[dict]) -> str:
    q = keywords(question)
    if not q:
        return NOT_FOUND
    scored, seen = [], set()
    for c in chunks:
        n, snip = _best_window(c["text"], q)
        key = (c["doc_name"], c["page_number"])
        if n and key not in seen:
            seen.add(key)
            scored.append((n, snip, c["doc_name"], c["page_number"]))
    if not scored:
        return NOT_FOUND
    scored.sort(key=lambda x: -x[0])
    body = "\n\n".join(f"- {s} ({d}, Page {p})" for _, s, d, p in scored[:3])
    return "Note: the AI model is unavailable, so these are the closest passages.\n\n" + body


def generate_rag_answer(question: str, chunks: list[dict], history: list[dict] | None = None, available_docs: list[str] | None = None) -> str:
    if not chunks:
        return NOT_FOUND

    context_text = "\n\n".join(
        f"[Passage {i} | {c['doc_name']} | Page {c['page_number']}]\n{c['text']}"
        for i, c in enumerate(chunks, start=1)
    )

    doc_list = ", ".join(available_docs) if available_docs else "none"
    system_prompt = (
        f"You are SmartDoc AI, a document assistant. Available documents: {doc_list}.\n"
        "Answer like a thoughtful colleague who has read the document: natural, short paragraphs.\n"
        "1. Start with the direct answer (yes, no, or the fact) in the first sentence.\n"
        "2. Reason before answering. Check the numbers (add up durations, compare dates and totals) "
        "and say so briefly. If the person's premise is slightly off, e.g. they say 4 hours but the "
        "document says 4 hours 30 minutes, gently correct it and explain.\n"
        "3. Add only the supporting detail that matters. Use bullets only for a list of 3 or more "
        "parallel items. No headings, and do not repeat the question.\n"
        "4. Use ONLY the passages provided. Cite as (Document, Page N) once per group of related "
        "claims, not after every line. Never mention passage numbers.\n"
        "5. Use the conversation so far to resolve words like 'he', 'it' or 'that'.\n"
        "6. If the passages cover only part of the question, answer that part and say what is missing. "
        f"If nothing relevant is there, reply ONLY with: \"{NOT_FOUND}\""
    )
    user_prompt = f"Source passages:\n{context_text}\n\nQuestion: {question}"

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
                inferenceConfig={"temperature": 0.2, "maxTokens": 800},
            )
            return resp["output"]["message"]["content"][0]["text"].strip()
        except Exception as e:
            _bedrock_mark_down(e)

    gemini_key = os.getenv("GEMINI_API_KEY") or get_settings().get("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            convo = ""
            if history:
                lines = []
                for turn in history[-4:]:
                    who = "User" if turn.get("role") == "user" else "Assistant"
                    content = turn.get("content", "").strip()[:600]
                    if content:
                        lines.append(f"{who}: {content}")
                if lines:
                    convo = "Conversation so far:\n" + "\n".join(lines) + "\n\n"
            return _call_gemini(convo + user_prompt, system_prompt, gemini_key.strip())
        except Exception as e:
            print(f"[Gemini Fallback Triggered] {str(e)[:200]}", flush=True)

    return _extractive_fallback(question, chunks)
