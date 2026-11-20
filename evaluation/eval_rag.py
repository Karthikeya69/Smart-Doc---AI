#!/usr/bin/env python3
"""Automated RAG evaluation for SmartDoc AI (standard library only).

Runs the mandatory test cases against the LIVE API and checks, for each one:
  * the answer contains the expected facts,
  * the citations point to the expected PDF page,
  * out-of-scope questions return "Information not available" with no citations,
  * a private chat cannot read the main documents (document-level access control).

It also reports whether the answer was written by the AI model or by the labelled
extractive fallback, so you can see when the LLM was unavailable.

Usage (PowerShell):
    python tools\\eval_rag.py
    python tools\\eval_rag.py --url http://smartdoc-eduleem.duckdns.org --doc Sample_HR_Policy.pdf

Sample_HR_Policy.pdf must already be uploaded as a main document.
Writes tools\\eval_report.md next to this script. Exit code 0 = all passed.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

NOT_FOUND = "information not available in the uploaded documents"
FALLBACK_MARK = "the ai model is unavailable"

# expect_all: every term must appear in the answer. expect_any: at least one must appear.
# page: that page must be cited. min_pages: at least N different pages must be cited.
CASES = [
    {"id": "T1", "name": "Annual leave entitlement", "q": "What is the annual leave entitlement?",
     "expect_all": ["24"], "page": 1},
    {"id": "T2", "name": "Sick leave eligibility", "q": "How many sick leaves are employees eligible for?",
     "expect_all": ["12"], "page": 2},
    {"id": "T3", "name": "Maternity leave policy", "q": "What is the maternity leave policy?",
     "expect_all": ["26"], "page": 2},
    {"id": "T4", "name": "Notice period", "q": "What is the employee notice period?",
     "expect_all": ["60"], "page": 3},
    {"id": "T5", "name": "IT security guidelines", "q": "What are the company's IT security guidelines?",
     "expect_any": ["password", "multi-factor", "mfa"], "page": 3},
    {"id": "T6", "name": "Multi-section question", "q": "Compare annual leave, sick leave and casual leave",
     "expect_all": ["24", "12", "6"], "min_pages": 2},
    {"id": "T7", "name": "Not covered in document", "q": "What is the capital of France?",
     "expect_unavailable": True},
    {"id": "T8", "name": "Private chat is isolated", "q": "What is the annual leave entitlement?",
     "expect_unavailable": True, "scope": "isolated", "own_chat": True},
    {"id": "T9", "name": "Document summary", "q": "Give me a summary of Sample_HR_Policy.pdf",
     "expect_any": ["leave", "notice"], "min_pages": 2},
    {"id": "T10", "name": "Typo tolerance", "q": "What is the anual entitlment?",
     "expect_all": ["24"], "page": 1},
]


def post_chat(base_url, payload, timeout=120):
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def evaluate(case, data):
    answer = (data.get("answer") or "")
    low = answer.lower()
    cites = data.get("citations") or []
    pages = sorted({c.get("page_number") for c in cites if c.get("page_number") is not None})
    docs = sorted({c.get("doc_name") for c in cites if c.get("doc_name")})
    problems = []

    if case.get("expect_unavailable"):
        if NOT_FOUND not in low:
            problems.append("did not say the information is unavailable")
        if cites:
            problems.append(f"returned {len(cites)} citation(s) for an unavailable answer")
    else:
        for term in case.get("expect_all", []):
            if term.lower() not in low:
                problems.append(f"answer is missing '{term}'")
        if case.get("expect_any") and not any(t.lower() in low for t in case["expect_any"]):
            problems.append("answer has none of: " + ", ".join(case["expect_any"]))
        if case.get("page") and case["page"] not in pages:
            problems.append(f"page {case['page']} not cited (cited: {pages or 'none'})")
        if case.get("min_pages") and len(pages) < case["min_pages"]:
            problems.append(f"only {len(pages)} page(s) cited, expected {case['min_pages']}+")
        if NOT_FOUND in low:
            problems.append("answered 'not available' although the document covers it")

    ai = "fallback" if FALLBACK_MARK in low else ("n/a" if case.get("expect_unavailable") else "LLM")
    return not problems, problems, pages, docs, ai, answer


def main():
    ap = argparse.ArgumentParser(description="Automated RAG evaluation for SmartDoc AI")
    ap.add_argument("--url", default="http://smartdoc-eduleem.duckdns.org")
    ap.add_argument("--doc", default="Sample_HR_Policy.pdf", help="expected document name in citations")
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between questions (free-tier rate limits)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id = str(int(time.time()))
    print(f"SmartDoc AI - automated RAG evaluation  ({stamp})")
    print(f"Target: {args.url}\n")

    rows, details = [], []
    for i, case in enumerate(CASES):
        payload = {
            "question": case["q"],
            "chat_id": f"eval-{run_id}" if case.get("own_chat") else "global",
            "history": [],
            "scope": case.get("scope", "all"),
        }
        try:
            data = post_chat(args.url, payload)
            ok, problems, pages, docs, ai, answer = evaluate(case, data)
            if ok and not case.get("expect_unavailable") and args.doc not in docs:
                ok, problems = False, [f"{args.doc} not cited (cited: {docs or 'none'})"]
        except Exception as e:  # network / HTTP failure
            ok, problems, pages, docs, ai, answer = False, [f"request failed: {e}"], [], [], "n/a", ""
        rows.append((case["id"], case["name"], "PASS" if ok else "FAIL", pages, ai))
        details.append((case, ok, problems, answer))
        print(f"{case['id']}  {'PASS' if ok else 'FAIL'}  {case['name']:<28} pages={pages or '-'}  answered_by={ai}")
        for p in problems:
            print(f"        - {p}")
        if i < len(CASES) - 1:
            time.sleep(args.pause)

    passed = sum(1 for r in rows if r[2] == "PASS")
    llm = sum(1 for r in rows if r[4] == "LLM")
    fb = sum(1 for r in rows if r[4] == "fallback")
    print(f"\nResult: {passed}/{len(rows)} passed.  AI model answered {llm}, labelled fallback answered {fb}.")
    if fb:
        print("Some answers came from the extractive fallback: the AI model (Gemini) was unavailable for them.")

    lines = [
        "# Automated RAG evaluation report", "",
        f"- Run: {stamp}", f"- Target: {args.url}", f"- Result: **{passed}/{len(rows)} passed**",
        f"- AI model answered: {llm}, labelled extractive fallback answered: {fb}", "",
        "| ID | Test | Result | Pages cited | Answered by |", "|---|---|---|---|---|",
    ]
    for rid, name, res, pages, ai in rows:
        lines.append(f"| {rid} | {name} | {res} | {', '.join(map(str, pages)) or '-'} | {ai} |")
    lines += ["", "## Answers", ""]
    for case, ok, problems, answer in details:
        lines.append(f"### {case['id']} {case['name']} - {'PASS' if ok else 'FAIL'}")
        lines.append(f"Question: {case['q']}" + (f"  (scope: {case['scope']})" if case.get("scope") else ""))
        for p in problems:
            lines.append(f"- Problem: {p}")
        lines.append("")
        lines.append("> " + (answer.strip().replace("\n", "\n> ") or "(no answer)"))
        lines.append("")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Report written to {out}")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
