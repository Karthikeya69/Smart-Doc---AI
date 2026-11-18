import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_ALTS = {
    "pdf": ["pdf"],
    "docx": ["word document", "word doc", "word file", "ms word", "docx", "word", "doc"],
    "xml": ["xml"],
    "json": ["json"],
    "csv": ["csv"],
    "txt": ["plain text", "text file", "txt"],
    "md": ["markdown", "md"],
}

# Words that carry no topic. If nothing else is left after removing the format phrase,
# the message is a pure "convert the last answer" request and should not trigger a search.
_FILLER = {
    "i", "want", "to", "the", "a", "an", "it", "this", "that", "me", "please", "can", "you",
    "could", "would", "give", "send", "make", "create", "download", "export", "convert", "save",
    "get", "need", "now", "also", "and", "file", "format", "as", "in", "into", "of", "above",
    "answer", "previous", "last", "same", "response", "result", "again", "too", "its", "my",
}


def _build_pattern(fmt: str) -> re.Pattern:
    a = "|".join(sorted(_ALTS[fmt], key=len, reverse=True))
    return re.compile(
        rf"\b(?:in|as|into|to)\s+(?:an?\s+)?\.?(?:{a})\b(?:\s+(?:file|format|document))?"
        rf"|\b(?:{a})\s+(?:file|format|version|copy)\b"
        rf"|\b(?:make|create|convert|export|download|save|turn|give|send|get)\s+(?:me\s+)?"
        rf"(?:it\s+|this\s+|that\s+)?(?:as\s+|to\s+|in\s+|into\s+)?(?:an?\s+)?\.?(?:{a})\b",
        re.I,
    )


_COMPILED = {fmt: _build_pattern(fmt) for fmt in _ALTS}


def detect_format(question: str):
    """Returns (format or None, question without the format phrase, export_only)."""
    for fmt, rx in _COMPILED.items():
        m = rx.search(question)
        if m:
            clean = (question[:m.start()] + " " + question[m.end():]).strip()
            words = re.findall(r"[a-z0-9]+", clean.lower())
            export_only = all(w in _FILLER for w in words)
            return fmt, (clean or question), export_only
    return None, question, False


def _x(text) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or ""))


def _plain(text: str) -> str:
    t = _x(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s*[\*\u2022]\s+", "- ", t, flags=re.M)
    return t.strip()


def _payload(question, answer, citations):
    return {
        "question": _x(question),
        "answer": _plain(answer),
        "citations": [
            {
                "document": _x(c.get("doc_name", "")),
                "page": c.get("page_number"),
                "passage": _x(c.get("snippet", "")),
            }
            for c in citations
        ],
    }


def _pdf(p) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="SmartDoc Extract")
    st = getSampleStyleSheet()
    story = [
        Paragraph("SmartDoc AI - Extracted Information", st["Title"]),
        Spacer(1, 12),
        Paragraph(f"<b>Question:</b> {escape(p['question'])}", st["Normal"]),
        Spacer(1, 10),
        Paragraph("<b>Answer:</b>", st["Heading3"]),
        Paragraph(escape(p["answer"]).replace("\n", "<br/>"), st["Normal"]),
        Spacer(1, 14),
    ]
    if p["citations"]:
        story.append(Paragraph("<b>Sources:</b>", st["Heading3"]))
        for c in p["citations"]:
            story.append(Paragraph(f"<b>{escape(c['document'])} (Page {c['page']})</b>", st["Normal"]))
            story.append(Paragraph(f"<i>{escape(c['passage'])}</i>", st["Normal"]))
            story.append(Spacer(1, 8))
    doc.build(story)
    return buf.getvalue()


def _docx(p) -> bytes:
    d = Document()
    d.add_heading("SmartDoc AI - Extracted Information", 0)
    d.add_paragraph(f"Question: {p['question']}")
    d.add_heading("Answer", 1)
    for para in p["answer"].split("\n"):
        d.add_paragraph(para)
    if p["citations"]:
        d.add_heading("Sources", 1)
        for c in p["citations"]:
            d.add_paragraph(f"{c['document']} (Page {c['page']})", style="List Bullet")
            d.add_paragraph(c["passage"])
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _xml(p) -> bytes:
    root = ET.Element("smartdoc")
    ET.SubElement(root, "question").text = p["question"]
    ET.SubElement(root, "answer").text = p["answer"]
    srcs = ET.SubElement(root, "sources")
    for c in p["citations"]:
        s = ET.SubElement(srcs, "source", document=c["document"], page=str(c["page"]))
        s.text = c["passage"]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _json(p) -> bytes:
    return json.dumps(p, indent=2, ensure_ascii=False).encode("utf-8")


def _csv(p) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["question", "answer", "document", "page", "passage"])
    first = True
    for c in p["citations"] or [{"document": "", "page": "", "passage": ""}]:
        w.writerow([
            p["question"] if first else "",
            p["answer"] if first else "",
            c["document"], c["page"], c["passage"],
        ])
        first = False
    return out.getvalue().encode("utf-8-sig")


def _txt(p) -> bytes:
    lines = [f"Question: {p['question']}", "", "Answer:", p["answer"], ""]
    for c in p["citations"]:
        lines.append(f"- {c['document']} (Page {c['page']}): {c['passage']}")
    return "\n".join(lines).encode("utf-8")


def _md(p) -> bytes:
    lines = [
        "# SmartDoc AI - Extracted Information", "",
        f"**Question:** {p['question']}", "",
        "## Answer", p["answer"], "", "## Sources",
    ]
    for c in p["citations"]:
        lines.append(f"- **{c['document']} (Page {c['page']})**: {c['passage']}")
    return "\n".join(lines).encode("utf-8")


FILES = {
    "pdf": (_pdf, "application/pdf", "pdf"),
    "docx": (_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "xml": (_xml, "application/xml", "xml"),
    "json": (_json, "application/json", "json"),
    "csv": (_csv, "text/csv", "csv"),
    "txt": (_txt, "text/plain", "txt"),
    "md": (_md, "text/markdown", "md"),
}


def build(fmt: str, question: str, answer: str, citations: list[dict]):
    fn, mime, ext = FILES[fmt]  # KeyError if the format is unsupported
    return fn(_payload(question, answer, citations)), mime, ext