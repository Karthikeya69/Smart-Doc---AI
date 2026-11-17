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
    answer = generate_rag_answer(req.question, retrieved, req.history)

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


@app.post("/api/export/pdf")
def export_pdf(req: ExportPdfRequest):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="SmartDoc AI Extract")
    styles = getSampleStyleSheet()
    story = [
        Paragraph("SmartDoc AI — Extracted Information", styles["Title"]),
        Spacer(1, 12),
        Paragraph(f"<b>Query:</b> {escape(req.question)}", styles["Normal"]),
        Spacer(1, 10),
        Paragraph("<b>Grounded Answer:</b>", styles["Heading3"]),
        Paragraph(escape(req.answer).replace("\n", "<br/>"), styles["Normal"]),
        Spacer(1, 14),
    ]
    if req.citations:
        story.append(Paragraph("<b>Source Citations:</b>", styles["Heading3"]))
        for c in req.citations:
            ref = f"{escape(str(c.get('doc_name', '')))} (Page {escape(str(c.get('page_number', '')))})"
            story.append(Paragraph(f"<b>{ref}</b>", styles["Normal"]))
            story.append(Paragraph(f"<i>\"{escape(str(c.get('snippet', '')))}\"</i>", styles["Normal"]))
            story.append(Spacer(1, 8))

    doc.build(story)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="smartdoc-extract.pdf"'},
    )