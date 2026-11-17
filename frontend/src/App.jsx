import { useEffect, useState } from "react";

const QUICK_PROMPTS = [
  "What is the annual leave entitlement?",
  "How many sick leaves are employees eligible for?",
  "What is the maternity leave policy?",
  "What is the employee notice period?",
];

export default function App() {
  const [documents, setDocuments] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [searchDoc, setSearchDoc] = useState("");
  const [chats, setChats] = useState(() => {
    try {
      const saved = localStorage.getItem("smartdoc_chats");
      return saved ? JSON.parse(saved) : [{ id: Date.now(), title: "New chat", messages: [] }];
    } catch {
      return [{ id: Date.now(), title: "New chat", messages: [] }];
    }
  });
  const [activeChatId, setActiveChatId] = useState(() => chats[0]?.id);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState("pdf");
  const [activePdf, setActivePdf] = useState({ name: "", page: 1, url: "" });
  const [activeCitations, setActiveCitations] = useState([]);

  const activeChat = chats.find((c) => c.id === activeChatId) || chats[0];
  const messages = activeChat?.messages || [];

  useEffect(() => {
    try {
      localStorage.setItem("smartdoc_chats", JSON.stringify(chats.slice(0, 12)));
    } catch {}
  }, [chats]);

  const fetchDocuments = async () => {
    try {
      const res = await fetch("/api/documents");
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch {}
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  const startNewChat = () => {
    const newId = Date.now();
    setChats((prev) => [{ id: newId, title: "New chat", messages: [] }, ...prev]);
    setActiveChatId(newId);
    setActiveCitations([]);
  };

  const handleUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploadError("");
    setUploading(true);

    for (const file of files) {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch("/api/documents/upload", { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok) setUploadError(data.detail || `Failed: ${file.name}`);
      } catch (err) {
        setUploadError(`Upload error: ${err.message}`);
      }
    }
    await fetchDocuments();
    setUploading(false);
    e.target.value = "";
  };

  const openPdfAtPage = (name, url, page = 1) => {
    const finalUrl = url.includes("#page=") ? url : `${url}#page=${page}`;
    setActivePdf({ name, page, url: finalUrl });
    setInspectorTab("pdf");
    setInspectorOpen(true);
  };

  const downloadExtractPdf = async (qText, msg) => {
    try {
      const res = await fetch("/api/export/pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: qText || "Extracted Information",
          answer: msg.content,
          citations: msg.citations || [],
        }),
      });
      if (!res.ok) return;
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "smartdoc-extract.pdf";
      a.click();
      window.URL.revokeObjectURL(url);
    } catch {}
  };

  const sendQuestion = async (textOverride) => {
    const trimmed = (textOverride ?? question).trim();
    if (!trimmed || asking) return;

    const historyPayload = messages.slice(-6).map((m) => ({ role: m.role, content: m.content }));
    const userMsg = { role: "user", content: trimmed, citations: [] };

    setChats((prev) =>
      prev.map((c) =>
        c.id === activeChat.id
          ? {
              ...c,
              title: c.messages.length === 0 ? trimmed.slice(0, 32) : c.title,
              messages: [...c.messages, userMsg],
            }
          : c
      )
    );
    setQuestion("");
    setAsking(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed, history: historyPayload }),
      });
      const data = await res.json();
      const assistantMsg = res.ok
        ? { role: "assistant", content: data.answer, citations: data.citations || [], query: trimmed }
        : { role: "assistant", content: `Error: ${data.detail || "Request failed"}`, citations: [], query: trimmed };

      setChats((prev) =>
        prev.map((c) => (c.id === activeChat.id ? { ...c, messages: [...c.messages, assistantMsg] } : c))
      );
      if (assistantMsg.citations.length > 0) {
        setActiveCitations(assistantMsg.citations);
        setInspectorTab("sources");
        setInspectorOpen(true);
      }
    } catch (err) {
      setChats((prev) =>
        prev.map((c) =>
          c.id === activeChat.id
            ? { ...c, messages: [...c.messages, { role: "assistant", content: `Network error: ${err.message}`, citations: [] }] }
            : c
        )
      );
    } finally {
      setAsking(false);
    }
  };

  const filteredDocs = documents.filter((d) => d.name.toLowerCase().includes(searchDoc.toLowerCase()));

  return (
    <div className="shell">
      {/* Left Sidebar */}
      <aside className="sidebar">
        <div className="sb-top">
          <div className="brand-row">
            <div className="brand-icon">S</div>
            <span className="brand-name">SmartDoc AI</span>
            <span className="pro-pill">RAG</span>
          </div>

          <button className="new-chat-btn" onClick={startNewChat}>
            <span>+ New chat</span>
          </button>

          <input
            className="search-input"
            type="text"
            placeholder="Search documents..."
            value={searchDoc}
            onChange={(e) => setSearchDoc(e.target.value)}
          />
        </div>

        <div className="sb-section">
          <div className="sb-label-row">
            <span className="sb-label">Documents ({documents.length})</span>
            <label className={`mini-upload ${uploading ? "disabled" : ""}`}>
              {uploading ? "Uploading..." : "+ Add PDF"}
              <input type="file" accept=".pdf,application/pdf" multiple disabled={uploading} onChange={handleUpload} hidden />
            </label>
          </div>
          {uploadError && <div className="error-box">{uploadError}</div>}
          <ul className="nav-list">
            {filteredDocs.map((doc) => (
              <li
                key={doc.name}
                className="nav-item"
                onClick={() => doc.view_url && openPdfAtPage(doc.name, doc.view_url, 1)}
                title={doc.status}
              >
                <span className={`dot ${doc.status === "ready" ? "green" : doc.status === "processing" ? "amber" : "red"}`} />
                <span className="nav-text">{doc.name}</span>
                <span className="nav-count">{doc.pages || 0}p</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="sb-section">
          <div className="sb-label">Recent Chats</div>
          <ul className="nav-list">
            {chats.map((c) => (
              <li
                key={c.id}
                className={`nav-item ${c.id === activeChat?.id ? "active" : ""}`}
                onClick={() => setActiveChatId(c.id)}
              >
                <span className="nav-text">{c.title}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="sb-footer">
          <div className="avatar">SD</div>
          <div>
            <div className="user-name">SmartDoc Workspace</div>
            <div className="user-sub">S3 · Textract · Bedrock</div>
          </div>
        </div>
      </aside>

      {/* Main Center Stage */}
      <main className="main-pane">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="sep">/</span>
            <strong>{activeChat?.title || "New chat"}</strong>
          </div>
          <button className="toggle-inspector" onClick={() => setInspectorOpen((v) => !v)}>
            {inspectorOpen ? "Hide Inspector" : "Show PDF & Sources"}
          </button>
        </header>

        {messages.length === 0 ? (
          <div className="start-stage">
            <div className="orb" />
            <h1 className="hero-title">Ask anything about your PDFs</h1>
            <p className="hero-sub">Upload HR policies, manuals, or contracts. Every answer is grounded with page citations.</p>

            <form
              className="composer-card"
              onSubmit={(e) => {
                e.preventDefault();
                sendQuestion();
              }}
            >
              <textarea
                rows={2}
                placeholder="Ask a question about your uploaded documents..."
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    sendQuestion();
                  }
                }}
              />
              <div className="composer-bar">
                <div className="pills">
                  <label className="pill-btn">
                    + Attach PDF
                    <input type="file" accept=".pdf,application/pdf" multiple disabled={uploading} onChange={handleUpload} hidden />
                  </label>
                  <span className="pill-active">Vector RAG</span>
                  <span className="pill-muted">{documents.filter((d) => d.status === "ready").length} docs ready</span>
                </div>
                <button className="send-circle" type="submit" disabled={asking || !question.trim()}>
                  ↑
                </button>
              </div>
            </form>

            <div className="quick-grid">
              {QUICK_PROMPTS.map((qp) => (
                <button key={qp} className="quick-chip" onClick={() => sendQuestion(qp)}>
                  {qp}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="chat-scroll">
              {messages.map((m, idx) => (
                <div key={idx} className={`msg-row ${m.role}`}>
                  <div className="msg-card">
                    <div className="msg-body">{m.content}</div>
                    {m.role === "assistant" && (
                      <div className="msg-actions">
                        <button className="action-btn" onClick={() => downloadExtractPdf(m.query, m)}>
                          ↓ Download Extract as PDF
                        </button>
                        {m.citations?.length > 0 && (
                          <button
                            className="action-btn"
                            onClick={() => {
                              setActiveCitations(m.citations);
                              setInspectorTab("sources");
                              setInspectorOpen(true);
                            }}
                          >
                            Sources ({m.citations.length})
                          </button>
                        )}
                      </div>
                    )}

                    {m.citations && m.citations.length > 0 && (
                      <div className="cite-strip">
                        {m.citations.map((c, cIdx) => (
                          <button
                            key={cIdx}
                            className="cite-pill"
                            onClick={() => openPdfAtPage(c.doc_name, c.view_url, c.page_number)}
                          >
                            {c.doc_name} · p.{c.page_number}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {asking && (
                <div className="msg-row assistant">
                  <div className="msg-card muted">Searching vector index and generating grounded answer...</div>
                </div>
              )}
            </div>

            <form
              className="bottom-composer"
              onSubmit={(e) => {
                e.preventDefault();
                sendQuestion();
              }}
            >
              <input
                type="text"
                placeholder="Ask a follow-up question..."
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                disabled={asking}
              />
              <button className="send-circle" type="submit" disabled={asking || !question.trim()}>
                ↑
              </button>
            </form>
          </>
        )}
      </main>

      {/* Right Split-Shell Inspector */}
      {inspectorOpen && (
        <aside className="inspector">
          <div className="insp-tabs">
            <button className={inspectorTab === "pdf" ? "active" : ""} onClick={() => setInspectorTab("pdf")}>
              PDF Viewer
            </button>
            <button className={inspectorTab === "sources" ? "active" : ""} onClick={() => setInspectorTab("sources")}>
              Sources ({activeCitations.length})
            </button>
          </div>

          {inspectorTab === "pdf" ? (
            <div className="insp-body">
              {activePdf.url ? (
                <>
                  <div className="pdf-bar">
                    <span>{activePdf.name} (Page {activePdf.page})</span>
                    <a href={activePdf.url} target="_blank" rel="noreferrer">Open ↗</a>
                  </div>
                  <iframe key={activePdf.url} src={activePdf.url} title="PDF Preview" className="pdf-frame" />
                </>
              ) : (
                <div className="insp-empty">Click any document or citation to view the PDF at its exact page.</div>
              )}
            </div>
          ) : (
            <div className="insp-body scroll">
              {activeCitations.length === 0 ? (
                <div className="insp-empty">Ask a question to inspect retrieved source passages.</div>
              ) : (
                activeCitations.map((c, i) => (
                  <div key={i} className="source-card">
                    <div className="source-head">
                      <strong>{c.doc_name} · Page {c.page_number}</strong>
                      <button onClick={() => openPdfAtPage(c.doc_name, c.view_url, c.page_number)}>
                        Jump to p.{c.page_number} ↗
                      </button>
                    </div>
                    <p>"{c.snippet}"</p>
                  </div>
                ))
              )}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}