from io import BytesIO
from xml.sax.saxutils import escape
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services.s3_service import upload_file_to_s3, get_pdf_presigned_url
from app.services.textract_service import (
    extract_pages_with_textract,
    extract_image_with_textract,
    chunk_pages,
)
from app.services.bedrock_service import generate_rag_answer
from app.services.vector_service import (
    delete_document,
    get_documents,
    index_document_chunks,
    load_index_from_s3,
    search_chunks,
    upsert_document_meta,
)

app = FastAPI(title="SmartDoc AI API", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTS = (".pdf", ".png", ".jpg", ".jpeg")


@app.on_event("startup")
def startup_event():
    load_index_from_s3()


class ChatRequest(BaseModel):
    question: str
    chat_id: str = "global"
    history: list[dict] = []


class ExportPdfRequest(BaseModel):
    question: str = "Extracted Document Summary"
    answer: str
    citations: list[dict] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/documents")
def list_documents(chat_id: str = "global"):
    docs = get_documents(chat_id)
    for d in docs:
        if d.get("status") == "ready":
            d["view_url"] = get_pdf_presigned_url(d["name"])
    return {"documents": docs}


@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), chat_id: str = Form("global")):
    fname = file.filename or ""
    lower = fname.lower()
    if not lower.endswith(ALLOWED_EXTS):
        raise HTTPException(status_code=400, detail="Only PDF, PNG, JPG, and JPEG files are supported.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    is_pdf = lower.endswith(".pdf")
    ctype = "application/pdf" if is_pdf else ("image/png" if lower.endswith(".png") else "image/jpeg")

    meta = {
        "name": fname,
        "chat_id": chat_id,
        "type": "pdf" if is_pdf else "image",
        "status": "processing",
        "pages": 0,
        "chunks": 0,
        "view_url": "",
    }
    upsert_document_meta(meta)

    try:
        s3_key = upload_file_to_s3(content, fname, content_type=ctype)
        pages = extract_pages_with_textract(s3_key) if is_pdf else extract_image_with_textract(content)
        chunks = chunk_pages(fname, pages, chat_id=chat_id)
        index_document_chunks(fname, chunks)

        meta.update({
            "status": "ready",
            "pages": len(pages),
            "chunks": len(chunks),
            "view_url": get_pdf_presigned_url(fname),
        })
        upsert_document_meta(meta)
        return meta
    except Exception as e:
        meta["status"] = f"failed: {e}"
        upsert_document_meta(meta)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{doc_name}")
def remove_document(doc_name: str):
    delete_document(doc_name)
    return {"deleted": doc_name}


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    retrieved = search_chunks(req.question, chat_id=req.chat_id, top_k=4)
    available_docs = [d["name"] for d in get_documents(req.chat_id) if d.get("status") == "ready"]
    answer = generate_rag_answer(req.question, retrieved, req.history, available_docs)

    unavailable = "information not available in the uploaded documents" in answer.lower()
    citations = []
    if not unavailable:
        for c in retrieved:
            base_url = get_pdf_presigned_url(c["doc_name"])
            citations.append({
                "doc_name": c["doc_name"],
                "page_number": c["page_number"],
                "snippet": c["text"],
                "score": c["score"],
                "view_url": f"{base_url}#page={c['page_number']}",
            })

    return {"answer": answer, "citations": citations}


import re as _re
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle


def _smart_pdf_filename(question: str, answer: str) -> str:
    source = question.strip() if question and len(question.strip()) > 3 else answer[:50]
    cleaned = _re.sub(r"\b(extract|download|create|make|give|me|as|a|the|pdf|info|information|content|about|what|is|are|can|you|tell)\b", " ", source.lower())
    slug = _re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    if not slug:
        slug = "smartdoc-extract"
    return f"{slug[:42]}.pdf"


@app.post("/api/export/pdf")
def export_pdf(req: ExportPdfRequest):
    filename = _smart_pdf_filename(req.question, req.answer)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=filename)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("SmartDoc AI â€” Mapped Document Extract", styles["Title"]),
        Spacer(1, 10),
        Paragraph(f"<b>Topic / Query:</b> {escape(req.question)}", styles["Normal"]),
        Spacer(1, 12),
        Paragraph("<b>Extracted &amp; Organized Information:</b>", styles["Heading3"]),
    ]

    for line in req.answer.splitlines():
        clean_line = line.strip()
        if clean_line:
            story.append(Paragraph(escape(clean_line), styles["Normal"]))
            story.append(Spacer(1, 5))

    if req.citations:
        story.append(Spacer(1, 12))
        story.append(Paragraph("<b>Mapped Source References:</b>", styles["Heading3"]))
        table_data = [["Document", "Page", "Supporting Passage"]]
        for c in req.citations:
            snippet = str(c.get("snippet", ""))[:180] + ("..." if len(str(c.get("snippet", ""))) > 180 else "")
            table_data.append([
                Paragraph(escape(str(c.get("doc_name", ""))), styles["Normal"]),
                str(c.get("page_number", "")),
                Paragraph(escape(snippet), styles["Normal"]),
            ])
        tbl = Table(table_data, colWidths=[120, 45, 310])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18181b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(tbl)

    doc.build(story)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Suggested-Filename": filename,
            "Access-Control-Expose-Headers": "Content-Disposition, X-Suggested-Filename",
        },
    )