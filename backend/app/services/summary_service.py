"""Overview and summary questions: "what is in the assignment?", "summarize this document", "summary of page 2".

These questions have no topic words to search for, so the normal keyword search finds nothing and the app
would answer "Information not available". Here we read the pages of the relevant document(s) directly
(spread across all pages), ask the AI model for a faithful summary, and cite one passage per page.

Questions that name a topic ("summarize the maternity leave policy") are NOT handled here; they go through
the normal search, which retrieves that topic's passages.
"""
import os
import re
from collections import OrderedDict

from app.config import get_settings
from app.services import bedrock_service as bs
from app.services.vector_service import get_doc_chunks, get_documents
from app.text_utils import keywords

NOT_FOUND = bs.NOT_FOUND
MAX_CHUNKS = 18        # passages sent to the model for a whole-document summary
MAX_PAGE_CHUNKS = 8    # passages for a single-page summary
PASSAGE_CHARS = 1200

_DOC = r"(?:document|documents|doc|docs|pdf|pdfs|file|files|attachment|attachments|assignment|report|handbook|policy)"
_PATTERNS = [re.compile(p, re.I) for p in [
    r"\b(?:summar(?:y|ies|ise|ize|ising|izing)|overview|tl;?\s?dr|gist|synopsis)\b",
    r"\b(?:key|main|important|major)\s+(?:points|takeaways|topics|sections|highlights)\b",
    r"\boutline\b",
    rf"\bwhat(?:'s|\s+is|\s+are)?\s+(?:there\s+|all\s+)?(?:in|inside|within)\s+(?:this|the|that|my|these|those|our)\s+{_DOC}\b",
    rf"\bwhat(?:'s|\s+is)?\s+(?:this|that|the)\s+{_DOC}\s+(?:about|for)\b",
    rf"\b(?:this|that|the)\s+{_DOC}\s+(?:is\s+)?about\b",
    rf"\b(?:describe|explain|tell\s+me\s+about|talk\s+about)\s+(?:this|that|the|my)\s+{_DOC}\b",
    rf"\bwhat\s+does\s+(?:this|that|the)\s+{_DOC}\s+(?:say|contain|cover|include)\b",
    r"\bwhat(?:'s|\s+is|\s+are)?\s+(?:on|in)\s+page\s*\d{1,3}\b",
]]
_PAGE_RE = re.compile(r"\bpage\s*(?:no\.?|number|#)?\s*(\d{1,3})\b", re.I)

# Words that only say "give me an overview" or name the document, not a topic to search for.
_GENERIC = {
    "summary", "summaries", "summarize", "summarise", "summarizing", "summarising", "overview", "tldr", "gist",
    "synopsis", "outline", "key", "main", "important", "major", "points", "takeaways", "topics", "sections",
    "highlights", "document", "documents", "doc", "docs", "pdf", "pdfs", "file", "files", "attachment",
    "attachments", "uploaded", "upload", "assignment", "report", "handbook", "policy", "policies", "brief",
    "short", "quick", "detailed", "whole", "entire", "complete", "full", "overall", "page", "pages", "content",
    "contents", "describe", "explain", "give", "show", "list", "there", "inside", "within", "say", "contain",
    "cover", "include", "talk",
}

SYSTEM_PROMPT = (
    "You are SmartDoc AI, a document assistant. Write a clear, faithful summary of the document passages provided.\n"
    "1. First sentence: say what the document is (type, purpose, who it is for) if the passages show it.\n"
    "2. Then 4 to 8 short bullets covering the main sections or points, in document order. "
    "End each bullet with (Document, Page N).\n"
    "3. Use ONLY the passages. Never invent details. The passages come from OCR and may contain small errors; "
    "read through them.\n"
    "4. If the user asked for something specific (one page, a length, a language), follow it. "
    "Reply in the language of the user's question.\n"
    f"5. If the passages are empty or unreadable, reply ONLY with: \"{NOT_FOUND}\""
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _stem(name: str) -> str:
    return _norm(re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name))


def _named_docs(question: str, docs: list[dict]) -> list[dict]:
    """Documents whose file name is mentioned in the question."""
    qn = f" {_norm(question)} "
    out = []
    for d in docs:
        stem = _stem(d["name"])
        if not stem:
            continue
        toks = [t for t in stem.split() if len(t) > 2]
        if f" {stem} " in qn or (len(toks) >= 2 and sum(1 for t in toks if f" {t} " in qn) >= 2):
            out.append(d)
    return out


def _sample(chunks: list[dict], limit: int) -> list[dict]:
    """Spread the passages over all pages, in document order."""
    by_page: "OrderedDict[tuple, list]" = OrderedDict()
    for c in chunks:
        by_page.setdefault((c["doc_name"], c["page_number"]), []).append(c)
    groups = list(by_page.values())
    picked: list[dict] = []
    if len(groups) > limit:  # more pages than we can send: take evenly spaced pages
        for i in range(limit):
            picked.append(groups[int(i * len(groups) / limit)][0])
    else:
        depth = 0
        while len(picked) < limit and any(depth < len(g) for g in groups):
            for g in groups:
                if depth < len(g) and len(picked) < limit:
                    picked.append(g[depth])
            depth += 1
    order = {k: n for n, k in enumerate(by_page)}
    picked.sort(key=lambda c: order[(c["doc_name"], c["page_number"])])
    return picked


def _complete(system: str, user: str):
    """Ask the AI model (Bedrock first, then Gemini). Returns None if no model answered."""
    if bs._bedrock_available():
        try:
            client, cfg = bs._bedrock_client()
            resp = client.converse(
                modelId=cfg["CHAT_MODEL"],
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"temperature": 0.2, "maxTokens": 900},
            )
            return resp["output"]["message"]["content"][0]["text"].strip()
        except Exception as e:
            bs._bedrock_mark_down(e)
    key = (os.getenv("GEMINI_API_KEY") or get_settings().get("GEMINI_API_KEY", "") or "").strip()
    if key and not bs.gemini_cooling():
        # Direct call (same HTTP helper the embeddings use), so it does not depend on the chat helper's signature.
        for model in (os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip(),):
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
            }
            try:
                data = bs._gemini_http(f"{bs.GEMINI_BASE}/models/{model}:generateContent", key, payload, timeout=30)
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    print(f"[Summary OK] {model}", flush=True)
                    return text
            except Exception as e:
                body = ""
                if hasattr(e, "read"):
                    try:
                        body = e.read().decode("utf-8", "ignore")[:200]
                    except Exception:
                        pass
                if "thinking" in body.lower():
                    payload["generationConfig"].pop("thinkingConfig", None)
                    try:  # this model rejects the setting: retry once without it
                        data = bs._gemini_http(f"{bs.GEMINI_BASE}/models/{model}:generateContent", key, payload, timeout=30)
                        text = "".join(x.get("text", "") for x in data["candidates"][0]["content"]["parts"]).strip()
                        if text:
                            return text
                    except Exception:
                        pass
                if getattr(e, "code", None) == 429:
                    bs.gemini_cool_down(body)
                print(f"[Summary Gemini {model} failed] {str(e)[:120]} {body}", flush=True)
    return None


def _outline_fallback(picked: list[dict]) -> str:
    lines = []
    seen = set()
    for c in picked:
        key = (c["doc_name"], c["page_number"])
        if key in seen:
            continue
        seen.add(key)
        first = " ".join(c["text"].split())[:170]
        lines.append(f"- {first} ({c['doc_name']}, Page {c['page_number']})")
    return ("Note: the AI model is unavailable, so this is only a rough outline "
            "(the start of each page).\n\n" + "\n".join(lines))


def try_summary(question: str, chat_id: str, scope: str):
    """Returns None when the question is not an overview request (normal search handles it).
    Otherwise returns (answer, citation_chunks)."""
    q = question.strip()
    if not any(p.search(q) for p in _PATTERNS):
        return None

    docs_all = [d for d in get_documents(chat_id) if d.get("status") == "ready"]
    own = [d for d in docs_all if str(d.get("chat_id", "global")) == str(chat_id)]
    visible = own if scope == "isolated" else docs_all

    named = _named_docs(q, visible)
    named_tokens = {t for d in named for t in _stem(d["name"]).split()}
    leftover = {w for w in keywords(q) if w not in _GENERIC and w not in named_tokens}
    if leftover:
        return None  # it names a topic, so the normal search handles it

    targets = named or own or visible
    if not targets:
        return NOT_FOUND, []

    page_m = _PAGE_RE.search(q)
    page = int(page_m.group(1)) if page_m else None
    chunks = get_doc_chunks([d["name"] for d in targets], chat_id)
    if page is not None:
        chunks = [c for c in chunks if c.get("page_number") == page]
    if not chunks:
        return NOT_FOUND, []

    picked = _sample(chunks, MAX_PAGE_CHUNKS if page is not None else MAX_CHUNKS)
    context = "\n\n".join(
        f"[Passage {i} | {c['doc_name']} | Page {c['page_number']}]\n{c['text'][:PASSAGE_CHARS]}"
        for i, c in enumerate(picked, start=1)
    )
    names = ", ".join(d["name"] for d in targets)
    answer = _complete(SYSTEM_PROMPT, f"Documents: {names}\n\nPassages:\n{context}\n\nRequest: {q}")
    if not answer:
        answer = _outline_fallback(picked)

    # One citation per page, so the Sources list shows exactly which pages were read.
    cites, seen = [], set()
    for c in picked:
        key = (c["doc_name"], c["page_number"])
        if key not in seen:
            seen.add(key)
            cites.append({"doc_name": c["doc_name"], "page_number": c["page_number"], "text": c["text"], "score": None})
    return answer, cites
