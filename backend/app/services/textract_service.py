import time
import boto3
from app.config import get_settings


def extract_pages_with_textract(s3_key: str) -> dict[int, str]:
    cfg = get_settings()
    client = boto3.client("textract", region_name=cfg["AWS_REGION"])

    start = client.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": cfg["S3_BUCKET"], "Name": s3_key}}
    )
    job_id = start["JobId"]

    # Poll until Textract finishes processing all PDF pages
    while True:
        resp = client.get_document_text_detection(JobId=job_id)
        status = resp["JobStatus"]
        if status == "SUCCEEDED":
            break
        if status == "FAILED":
            raise RuntimeError(f"Textract failed: {resp.get('StatusMessage', 'Unknown error')}")
        time.sleep(1.5)

    pages: dict[int, list[str]] = {}
    next_token = None
    while True:
        kwargs = {"JobId": job_id}
        if next_token:
            kwargs["NextToken"] = next_token
        page_resp = client.get_document_text_detection(**kwargs)

        for block in page_resp.get("Blocks", []):
            if block.get("BlockType") == "LINE":
                p = block.get("Page", 1)
                pages.setdefault(p, []).append(block.get("Text", ""))

        next_token = page_resp.get("NextToken")
        if not next_token:
            break

    return {p: "\n".join(lines) for p, lines in sorted(pages.items())}


def chunk_pages(doc_name: str, pages: dict[int, str], chunk_size: int = 700, overlap: int = 120) -> list[dict]:
    chunks = []
    for page_num, text in pages.items():
        clean = " ".join(text.split())
        if not clean:
            continue
        start = 0
        idx = 0
        while start < len(clean):
            end = start + chunk_size
            piece = clean[start:end]
            chunks.append({
                "id": f"{doc_name}__p{page_num}__c{idx}",
                "doc_name": doc_name,
                "page_number": page_num,
                "text": piece,
            })
            if end >= len(clean):
                break
            start += chunk_size - overlap
            idx += 1
    return chunks