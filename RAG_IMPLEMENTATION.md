# SmartDoc AI - RAG Implementation

This document explains how Retrieval-Augmented Generation (RAG) is implemented in SmartDoc AI, step by step, with the AWS service used for each step and the exact behaviour of the code. It follows the order of the tasks the evaluator asks for in the final demonstration.

## 0. Summary

**RAG** means the AI model does not answer from memory. The system first finds the passages of the uploaded documents that are relevant to the question, gives only those passages to the model, and tells the model to answer from them alone. This keeps answers grounded in the documents and makes it possible to show where each answer came from.

```
UPLOAD:   PDF > S3 > Textract (text per page) > chunks (with document + page) > vectors > index saved in S3
QUESTION: question > keywords + vector > search index > top 4 chunks > LLM (chunks only) > answer + citations
```

| Step | What happens | Service / code |
|---|---|---|
| 1. Store | Original file saved | Amazon S3 (`s3_service.py`) |
| 2. Extract | Text read per page | Amazon Textract (`textract_service.py`) |
| 3. Chunk | Page text split into pieces that keep document name and page | `chunk_pages` |
| 4. Embed | Each chunk and each question turned into a vector | `get_embedding` in `bedrock_service.py` |
| 5. Index | Chunks and vectors saved as one JSON index in S3 | `vector_service.py` |
| 6. Retrieve | Top 4 matching chunks found | `search_chunks` |
| 7. Generate | LLM writes an answer from those chunks only | `generate_rag_answer` |
| 8. Cite | Document, page, passage and a link to the page are returned | `_build_citations` in `main.py` |

## 1. Upload and storage (Amazon S3)

The browser sends the file to `POST /api/documents/upload`. The backend:

1. Accepts only `.pdf`, `.png`, `.jpg` and `.jpeg`. Anything else returns a clear error (invalid or unsupported documents are handled gracefully). An empty file is also rejected.
2. Records the document with status `processing`, so the interface can show it straight away.
3. Saves the original file in S3.
4. Runs extraction, chunking and indexing (sections 2 to 5).
5. Sets the status to `ready` with the page and chunk counts, or to `failed: <reason>` if something goes wrong.

When the user opens a citation, the backend creates a **presigned S3 link** with `#page=N` appended, so the PDF opens at the cited page. The bucket itself stays private.

## 2. How Amazon Textract extracts the content

*(Azure equivalent: Document Intelligence.)*

Textract is a managed service that reads documents and returns the text it finds, including text in scanned pages and photos. It works like OCR plus layout detection: it locates lines of text on each page and reads them.

In this project:
- For a **PDF**, the backend gives Textract the PDF stored in S3 and receives the text for each page, so every piece of text is tied to its **page number**.
- For a **PNG or JPG photo** (for example an invoice), the image is sent directly and treated as a single page.

The result is a mapping of `page number > text`. Page numbers are kept from this point on, because the citations depend on them.

## 3. How text chunking works

An entire document is too long to send to a model for every question and is a poor unit for searching. The text is therefore split into **chunks**, short passages of a few sentences.

Each chunk is stored with its metadata:

```
doc_name      Sample_HR_Policy.pdf
page_number   2
chat_id       global (or the chat it was attached to)
text          "Each employee is eligible for 12 days of paid sick leave per calendar year. ..."
embedding     [512 numbers]
```

Chunks are made **page by page**, so a chunk never mixes two pages. That is what allows an exact page reference in every answer. Small chunks give precise matches and cheap prompts; chunks that are too small lose context.

## 4. How embeddings work

An **embedding** turns text into a list of numbers (a vector) so that texts can be compared by computer. Two pieces of text that are similar should get vectors that are close together. Closeness is measured with **cosine similarity**. When vectors are normalised to length 1, cosine similarity is simply their dot product, which is what the code computes.

### What this project actually uses

The design is:
1. **Intended:** Amazon Bedrock Titan Text Embeddings (512 dimensions, normalised). The code tries this first.
2. **Currently active:** because Bedrock returns "Operation not allowed" on this account, the code uses a **local hashed bag-of-words vector** (`_local_embedding`). Each word is hashed into one of 512 positions, the counts are placed in those positions, and the vector is normalised.

Be clear about the difference when explaining it:
- The Titan embedding captures **meaning**, so "holiday" can match "leave".
- The hashed vector captures **which words appear**, so it behaves like keyword matching in vector form. It is real and working, but it is not semantic.

To avoid mixing the two, vectors made by one method must not be compared with vectors from the other. If Bedrock is unblocked later, delete the documents and upload them again.

## 5. The vector index (replaces Azure AI Search)

All chunks and their vectors are held in memory by each backend pod and saved as one JSON file in S3 (`index/smartdoc_index.json`).

- On startup each pod loads the index from S3.
- Every save records the S3 **ETag** (a fingerprint of the file). Before reading or changing the index, a pod checks the ETag, and if another pod has changed the file it reloads. This keeps all 3 pods consistent, so a document uploaded through one pod is visible through the others.

**Trade-off to state honestly:** this is a simple custom index, not a search service. It is fine for a few documents and demonstrates the concepts, but a managed service (OpenSearch, Azure AI Search) would be the choice for large collections.

## 6. How retrieval works (hybrid search)

For each question, `search_chunks` does this:

1. **Remove the format phrase.** "Give me the total marks in PDF" is searched as "total marks", so "PDF" does not distort the search. (`detect_format` in `exporters.py`.)
2. **Extract keywords.** Common words such as *what, is, the, can, you, tell, give* are removed (`text_utils.py`). "Can you tell me the deadline" becomes `{deadline}`.
3. **Keyword gate.** A chunk is considered only if it shares at least one keyword with the question.
4. **Score by vector similarity plus keyword overlap.**
   `score = cosine(question vector, chunk vector) + 0.15 x (shared keywords / question keywords)`
5. **Threshold and ranking.** Chunks with a score below 0.20 are dropped. The best 4 are kept.
6. **Scope.** Only chunks that are global or belong to the current chat are searched.

This is **hybrid search**: keyword matching and vector similarity combined. Because the search covers every uploaded document, it also supports **multi-document questions**.

## 7. How the answer is generated (prompt engineering)

The top chunks are labelled with their document and page and placed in the prompt. The system prompt tells the model to:

- start with the direct answer in the first sentence;
- check numbers and gently correct a wrong premise (for example "4 hours" when the document says 4 hours 30 minutes);
- use **only** the supplied passages, and cite them as (Document, Page N);
- use recent conversation turns to resolve words like "he", "it" and "that";
- answer the part that is covered and say what is missing when the passages only partly cover the question;
- reply exactly **"Information not available in the uploaded documents."** when nothing relevant is found.

### Which model answers

The backend tries three paths in order:

| Order | Path | Status |
|---|---|---|
| 1 | Amazon Bedrock, Nova Lite (`converse`) | Blocked on this account ("Operation not allowed") |
| 2 | Google Gemini API (`gemini-flash-latest`) | Active |
| 3 | Extractive fallback: closest passages with a note that no AI model was available | Used if Gemini fails |

After a Bedrock failure, Bedrock is skipped for 5 minutes so users are not slowed down by repeated failing calls. The Gemini key is sent in a request header, not in the URL, and is read from a Kubernetes Secret. The fallback is labelled so the interface never pretends to be an AI answer.

## 8. Source references and citations

Every answer that used the documents returns a list of citations. For each one the interface shows:

- the **original document name**,
- the **page number**,
- the **supporting text passage**,
- a button to **open the PDF at that page** in the viewer.

This lets the user verify where the information came from. Citations come from the retrieved chunks themselves, not from the model, so the document name and page number cannot be invented by the model.

## 9. Handling questions that are not covered (hallucination prevention)

Two layers protect against made-up answers:

1. **Retrieval layer.** A question such as "What is the capital of France?" shares no meaningful keyword with the documents, so no chunk passes the keyword gate. With no chunks, the backend returns "Information not available in the uploaded documents." without calling any model.
2. **Prompt layer.** If chunks are found but do not answer the question, the model is instructed to return the same sentence.

When the answer is "not available", the interface shows no citations and no download button.

## 10. Format on request (extra feature)

The user can ask for the answer in a file: "give me the leave policy in PDF", "as a word doc", "in xml". The backend detects the format with pattern matching (no AI call), removes the phrase before searching, and tells the interface which button to show. Supported formats: PDF, DOCX, XML, JSON, CSV, TXT and Markdown. A message that only asks for a format ("make it doc") converts the previous answer without searching again. If the user does not ask for a format, no download button appears.

## 11. Docker and container registry (ECR)

*(Azure equivalent: ACR.)*

- Each component has a **Dockerfile**: the backend (Python FastAPI) and the frontend (React build served by nginx).
- `docker build` turns the Dockerfile into an **image**, a packaged copy of the app with its dependencies.
- The image is tagged with the ECR repository address and sent with `docker push`.
- Kubernetes pulls the image from ECR when it starts a pod.

```powershell
docker build -t <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/smartdoc-backend:latest ./backend
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/smartdoc-backend:latest
```

## 12. Kubernetes on Amazon EKS

*(Azure equivalent: AKS.)*

- The cluster is created from `k8s/eks-cluster.yaml` with `eksctl`.
- Two **Deployments** run independently: frontend and backend (3 pods).
- **Services** give stable network addresses. The frontend service is a LoadBalancer that is reachable from the internet; nginx forwards `/api` to the backend service.
- **Readiness probes** tell Kubernetes when a pod is ready to receive traffic, and **liveness probes** restart a pod that stops responding. Both call `/health`.
- **Environment variables and secrets** (such as the Gemini key) are injected into the pods from a Kubernetes Secret.
- **Scaling:** `kubectl scale deployment smartdoc-backend --replicas=3` adds pods without any downtime.
- **Pod recovery:** if a pod is deleted, the Deployment creates a replacement automatically, and the other pods keep serving requests.

## 13. Security and monitoring

**What is in place**
- API keys live in a Kubernetes Secret and are not committed to GitHub (checked with `git grep` before each push).
- Presigned, time-limited S3 links are used to open PDFs; the bucket is not public.
- EKS control-plane logs are enabled in CloudWatch. Backend logs show which AI path answered.

**What is not finished (state this plainly)**
- Secrets Manager is configured, but the running pods read the key from a Kubernetes Secret.
- Pods use a broad node IAM role; least-privilege roles per pod (IRSA) are the next step.
- Container logs are not yet shipped to CloudWatch; only control-plane logs are.
- The site uses HTTP, not HTTPS.

## 14. Evaluation cheat sheet

How to answer each task the evaluator will ask for.

| Evaluator task | Where it is explained |
|---|---|
| Upload a new PDF | Section 1 |
| Explain how the document-extraction service reads content | Section 2 |
| Explain text chunking and embeddings | Sections 3 and 4 |
| Show how the search retrieves relevant content | Sections 5 and 6 |
| Ask questions and show answers with source references | Sections 7 and 8 |
| Show how questions not covered in the PDF are handled | Section 9 |
| Explain how Docker images are built and pushed | Section 11 |
| Show the application running on the cluster | Section 12 (`kubectl get pods`, `kubectl get svc`) |
| Scale from one pod to three | Section 12 |
| Explain security and monitoring | Section 13 |

**Say these three points at the start of the demo, before the evaluator asks:**
1. This is built on AWS, and the Azure services are mapped to AWS equivalents.
2. Bedrock was blocked on the account, so Gemini writes the answers, and the search index is a custom S3-backed index.
3. The embeddings are currently hashed word vectors; the design supports Titan embeddings once Bedrock is available.

## 15. Planned improvements

- Real semantic embeddings (Titan once Bedrock is unblocked, or a local model such as `bge-small` that runs inside the pod at no cost).
- Retry with a short wait when Gemini returns 503 or 429.
- A managed search service (OpenSearch) for large document sets.
- Least-privilege pod roles, Secrets Manager at runtime, HTTPS, and container logs in CloudWatch.
- Word, Excel and PowerPoint support.
