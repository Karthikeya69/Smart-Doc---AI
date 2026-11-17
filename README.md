# SmartDoc AI ? Intelligent PDF Document Assistant

Enterprise RAG application that ingests multi-page PDF documents, indexes page-tagged vector embeddings, answers natural-language questions with strict hallucination prevention, and provides clickable page-level citations (#page=N) plus downloadable PDF extracts.

## Architecture
User Browser -> AWS LoadBalancer -> React + Nginx Pod -> ClusterIP (smartdoc-backend-svc) -> FastAPI Pods (x3) -> Amazon S3 + Amazon Textract + Amazon Bedrock (Titan Embeddings V2 + Nova Lite) + AWS Secrets Manager

## RAG Implementation
1. PDF Storage: Uploaded PDFs stored in Amazon S3 (pdfs/<filename>) with inline disposition and CORS.
2. Page-Preserving Extraction: Amazon Textract (StartDocumentTextDetection) extracts LINE blocks grouped by exact Page number.
3. Chunking: 700-char chunks with 120-char overlap, keeping {doc_name, page_number} metadata.
4. Vector Indexing: Bedrock amazon.titan-embed-text-v2:0 (512-dim normalized), persisted in S3 (index/smartdoc_index.json).
5. Hybrid Retrieval & Grounding: Cosine similarity + keyword overlap retrieves top-4 passages for us.amazon.nova-lite-v1:0; out-of-scope queries return "Information not available in the uploaded documents."
6. Citations & PDF Export: Returns {doc_name, page_number, snippet, view_url#page=N} and /api/export/pdf.
