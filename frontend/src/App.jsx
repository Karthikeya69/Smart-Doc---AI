import { useEffect, useRef, useState } from "react";

const QUICK_PROMPTS = [
  "What is the annual leave entitlement?",
  "How many sick leaves are employees eligible for?",
  "What is the maternity leave policy?",
  "Extract all invoice details from the attached photo",
];

const HOVER_CSS = `
.hover-actions { display: inline-flex; gap: 6px; opacity: 0; transition: opacity 0.15s ease; }
.msg-card:hover .hover-actions,
.hover-actions:focus-within { opacity: 1; }
@media (hover: none) { .hover-actions { opacity: 1; } }
.edit-box { width: 100%; box-sizing: border-box; resize: vertical; min-height: 60px; background: transparent; color: inherit; border: 1px solid rgba(128,128,128,0.5); border-radius: 8px; padding: 8px 10px; font: inherit; line-height: 1.4; outline: none; }
.edit-box:focus { border-color: rgba(128,128,128,0.9); }
.edit-hint { font-size: 12px; opacity: 0.6; margin-top: 6px; }
.typing-dots { display: inline-flex; align-items: center; gap: 5px; padding: 6px 2px; }
.typing-dots span { width: 7px; height: 7px; border-radius: 50%; background: currentColor; opacity: 0.35; animation: typing-bounce 1.2s infinite ease-in-out; }
.typing-dots span:nth-child(2) { animation-delay: 0.15s; }
.typing-dots span:nth-child(3) { animation-delay: 0.3s; }
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.35; }
  30% { transform: translateY(-5px); opacity: 1; }
}
.scope-prompt { border: 1px solid rgba(128,128,128,0.45); border-radius: 12px; padding: 10px 12px; margin: 0 0 8px; }
.scope-title { font-weight: 600; font-size: 14px; }
.scope-sub { font-size: 12px; opacity: 0.7; margin: 4px 0 8px; line-height: 1.4; }
.scope-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.scope-badge { background: transparent; color: inherit; border: 1px solid rgba(128,128,128,0.45); border-radius: 999px; padding: 3px 10px; font-size: 12px; cursor: pointer; opacity: 0.85; }
.scope-badge:hover { opacity: 1; }
`;

// Convert-only exchanges ("make it doc") are not real Q&A, so they are kept out of the history sent to the backend.
const buildHistory = (msgs) => {
  const out = [];
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (msgs[i + 1]?.exportOnly) {
      i += 1;
      continue;
    }
    if (m.exportOnly) continue;
    out.push({ role: m.role, content: m.content });
  }
  return out.slice(-6);
};

const IconClose = () => (
  <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M2.5 2.5L9.5 9.5M9.5 2.5L2.5 9.5" />
  </svg>
);

const IconUp = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" />
  </svg>
);

const IconDown = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 3v10M3.5 8.5L8 13l4.5-4.5" />
  </svg>
);

export default function App() {
  const [documents, setDocuments] = useState([]);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadingName, setUploadingName] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [searchDoc, setSearchDoc] = useState("");
  const [copiedKey, setCopiedKey] = useState("");
  const [editingIdx, setEditingIdx] = useState(null);
  const [editText, setEditText] = useState("");
  const [askScope, setAskScope] = useState(false);
  const followUpRef = useRef(null);
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
  // "all" = main documents + this chat's files, "isolated" = this chat's files only
  const chatScope = activeChat?.scope === "isolated" ? "isolated" : "all";
  const chatOwnDocs = documents.filter(
    (d) => d.chat_id && d.chat_id !== "global" && String(d.chat_id) === String(activeChat?.id)
  );
  const scopePromptOpen = askScope && pendingAttachments.length > 0;

  useEffect(() => {
    try {
      localStorage.setItem("smartdoc_chats", JSON.stringify(chats.slice(0, 12)));
    } catch {}
  }, [chats]);

  // Auto-grow the follow-up box up to ~6 lines; shrink back after sending.
  useEffect(() => {
    const el = followUpRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }, [question, messages.length]);

  const fetchDocuments = async (chatIdOverride) => {
    const cid = String(chatIdOverride || activeChat?.id || "global");
    try {
      const res = await fetch(`/api/documents?chat_id=${encodeURIComponent(cid)}`);
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch {}
  };

  useEffect(() => {
    setPendingAttachments([]);
    setEditingIdx(null);
    setAskScope(false);
    fetchDocuments(activeChat?.id);
  }, [activeChatId]);

  const deleteDoc = async (e, name) => {
    e.stopPropagation();
    setPendingAttachments((prev) => prev.filter((p) => p.name !== name));
    await fetch(`/api/documents/${encodeURIComponent(name)}`, { method: "DELETE" });
    fetchDocuments(activeChat?.id);
  };

  const deleteChat = (e, id) => {
    e.stopPropagation();
    setChats((prev) => {
      const next = prev.filter((c) => c.id !== id);
      if (next.length === 0) {
        const fresh = { id: Date.now(), title: "New chat", messages: [] };
        setActiveChatId(fresh.id);
        return [fresh];
      }
      if (activeChatId === id) setActiveChatId(next[0].id);
      return next;
    });
  };

  const startNewChat = () => {
    const newId = Date.now();
    setChats((prev) => [{ id: newId, title: "New chat", messages: [] }, ...prev]);
    setActiveChatId(newId);
    setPendingAttachments([]);
    setActiveCitations([]);
    setEditingIdx(null);
    setAskScope(false);
  };

  const handleUpload = async (e, scope = "global") => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploadError("");
    setUploading(true);
    setUploadProgress(10);

    const targetChatId = scope === "chat" ? String(activeChat.id) : "global";

    for (const file of files) {
      setUploadingName(file.name);
      setUploadProgress(25);
      const timer = setInterval(() => {
        setUploadProgress((p) => (p < 88 ? p + 7 : p));
      }, 450);

      const formData = new FormData();
      formData.append("file", file);
      formData.append("chat_id", targetChatId);
      try {
        const res = await fetch("/api/documents/upload", { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok) {
          setUploadError(data.detail || `Failed: ${file.name}`);
        } else if (scope === "chat") {
          setPendingAttachments((prev) => [...prev.filter((p) => p.name !== data.name), data]);
          setAskScope(true);
        }
      } catch (err) {
        setUploadError(`Upload error: ${err.message}`);
      } finally {
        clearInterval(timer);
      }
    }

    setUploadProgress(100);
    await fetchDocuments(activeChat?.id);
    setTimeout(() => {
      setUploading(false);
      setUploadProgress(0);
      setUploadingName("");
    }, 400);
    e.target.value = "";
  };

  const openPdfAtPage = (name, url, page = 1) => {
    const finalUrl = url.includes("#page=") ? url : `${url}#page=${page}`;
    setActivePdf({ name, page, url: finalUrl });
    setInspectorTab("pdf");
    setInspectorOpen(true);
  };

  const downloadExtract = async (msg) => {
    const format = msg.exportFormat;
    if (!format) return;
    try {
      const res = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          format,
          question: msg.exportQuestion || msg.query || "Extracted Information",
          answer: msg.exportAnswer || msg.content,
          citations: msg.citations || [],
        }),
      });
      if (!res.ok) return;
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = res.headers.get("X-Suggested-Filename") || `smartdoc-extract.${format}`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch {}
  };

  const copyText = async (text, key) => {
    try {
      await navigator.clipboard.writeText(text || "");
    } catch {
      // Fallback for plain-HTTP pages, where navigator.clipboard is blocked
      const ta = document.createElement("textarea");
      ta.value = text || "";
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    setCopiedKey(key);
    setTimeout(() => setCopiedKey((k) => (k === key ? "" : k)), 1500);
  };

  const startEdit = (idx) => {
    setEditingIdx(idx);
    setEditText(messages[idx]?.content || "");
  };

  const cancelEdit = () => {
    setEditingIdx(null);
    setEditText("");
  };

  const submitEdit = () => {
    const text = editText.trim();
    if (!text || editingIdx === null || asking) return;
    const idx = editingIdx;
    setEditingIdx(null);
    setEditText("");
    sendQuestion(text, idx);
  };

  const setChatScope = (value) => {
    setChats((prev) => prev.map((c) => (c.id === activeChat.id ? { ...c, scope: value } : c)));
    setAskScope(false);
  };

  // baseIdx: when set, the conversation is cut back to messages[0..baseIdx) before sending (used by Edit).
  const sendQuestion = async (textOverride, baseIdx) => {
    const trimmed = (textOverride ?? question).trim();
    if (!trimmed || asking || scopePromptOpen) return;

    const priorMessages = baseIdx === undefined ? messages : messages.slice(0, baseIdx);
    const historyPayload = buildHistory(priorMessages);
    const sentFiles = baseIdx === undefined ? [...pendingAttachments] : [];
    const userMsg = {
      role: "user",
      content: trimmed,
      attachments: sentFiles.map((d) => ({ name: d.name, type: d.type, view_url: d.view_url })),
      citations: [],
    };

    setChats((prev) =>
      prev.map((c) => {
        if (c.id !== activeChat.id) return c;
        const prior = baseIdx === undefined ? c.messages : c.messages.slice(0, baseIdx);
        return {
          ...c,
          title: prior.length === 0 ? trimmed.slice(0, 32) : c.title,
          messages: [...prior, userMsg],
        };
      })
    );
    setPendingAttachments([]);
    if (baseIdx === undefined) setQuestion("");
    setAsking(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          chat_id: String(activeChat.id),
          history: historyPayload,
          scope: chatScope,
        }),
      });
      const data = await res.json();
      const assistantMsg = res.ok
        ? {
            role: "assistant",
            content: data.answer,
            citations: data.citations || [],
            query: trimmed,
            exportFormat: data.export_format || null,
            exportAnswer: data.export_answer || null,
            exportQuestion: data.export_question || null,
            exportOnly: !!data.export_answer,
          }
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

  const filteredDocs = documents.filter(
    (d) => (!d.chat_id || d.chat_id === "global") && d.name.toLowerCase().includes(searchDoc.toLowerCase())
  );

  const renderPendingChips = () =>
    pendingAttachments.length > 0 && (
      <div className="attach-chips-row">
        {pendingAttachments.map((doc) => (
          <div
            key={doc.name}
            className="attach-chip"
            onClick={() => doc.view_url && openPdfAtPage(doc.name, doc.view_url, 1)}
          >
            <span className="attach-icon">{doc.type === "image" ? "IMG" : "PDF"}</span>
            <span className="attach-name">{doc.name}</span>
            <button type="button" className="attach-x" onClick={(e) => deleteDoc(e, doc.name)} title="Remove">
              <IconClose />
            </button>
          </div>
        ))}
      </div>
    );

  const renderScopePrompt = () =>
    scopePromptOpen && (
      <div className="scope-prompt">
        <div className="scope-title">Is this file related to your main documents?</div>
        <div className="scope-sub">
          Related: the AI reads your main documents and this file together. Private: the AI reads only this file in
          this chat and ignores the main documents.
        </div>
        <div className="scope-actions">
          <button type="button" className="action-btn" onClick={() => setChatScope("all")}>
            Related (main docs + this file)
          </button>
          <button type="button" className="action-btn" onClick={() => setChatScope("isolated")}>
            Private (this file only)
          </button>
        </div>
      </div>
    );

  const renderScopeBadge = () =>
    chatOwnDocs.length > 0 && (
      <button
        type="button"
        className="scope-badge"
        title="Click to switch"
        onClick={() => setChatScope(chatScope === "isolated" ? "all" : "isolated")}
      >
        {chatScope === "isolated" ? "Private: this chat's files only" : "Using main docs + this chat's files"}
      </button>
    );

  return (
    <div className="shell">
      <style>{HOVER_CSS}</style>
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
            <span className="sb-label">Main documents ({filteredDocs.length})</span>
            <label className={`mini-upload ${uploading ? "disabled" : ""}`}>
              {uploading ? `${uploadProgress}%` : "+ Add Doc"}
              <input
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                multiple
                disabled={uploading}
                onChange={(e) => handleUpload(e, "global")}
                hidden
              />
            </label>
          </div>
          {uploading && (
            <div className="upload-progress-wrap">
              <div className="upload-progress-meta">
                <span>Processing {uploadingName}...</span>
                <span>{uploadProgress}%</span>
              </div>
              <div className="upload-progress-track">
                <div className="upload-progress-fill" style={{ width: `${uploadProgress}%` }} />
              </div>
            </div>
          )}
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
                <button type="button" className="del-btn" onClick={(e) => deleteDoc(e, doc.name)} title="Delete">
                  <IconClose />
                </button>
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
                <button type="button" className="del-btn" onClick={(e) => deleteChat(e, c.id)} title="Delete chat">
                  <IconClose />
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="sb-footer">
          <div className="avatar">SD</div>
          <div>
            <div className="user-name">SmartDoc Workspace</div>
            <div className="user-sub">EKS / S3 / Textract</div>
          </div>
        </div>
      </aside>

      <main className="main-pane">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="sep">/</span>
            <strong>{activeChat?.title || "New chat"}</strong>
          </div>
          {renderScopeBadge()}
          <button className="toggle-inspector" onClick={() => setInspectorOpen((v) => !v)}>
            {inspectorOpen ? "Hide Inspector" : "Show PDF & Sources"}
          </button>
        </header>

        {messages.length === 0 ? (
          <div className="start-stage">
            <div className="orb" />
            <h1 className="hero-title">Ask anything about your PDFs or Photos</h1>
            <p className="hero-sub">Upload HR policies, contracts, or invoice photos. Chat attachments stay private to this chat.</p>

            <form
              className="composer-card"
              onSubmit={(e) => {
                e.preventDefault();
                sendQuestion();
              }}
            >
              {renderPendingChips()}
              {renderScopePrompt()}
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
                    + Attach PDF / Photo
                    <input
                      type="file"
                      accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                      multiple
                      disabled={uploading}
                      onChange={(e) => handleUpload(e, "chat")}
                      hidden
                    />
                  </label>
                  <span className="pill-muted">{documents.filter((d) => d.status === "ready").length} ready</span>
                </div>
                <button className="send-circle" type="submit" disabled={asking || scopePromptOpen || !question.trim()}>
                  <IconUp />
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
              {messages.map((m, idx) => {
                const isUnavailable = m.content?.toLowerCase().includes("information not available");
                const showExportBtn = m.role === "assistant" && !isUnavailable && !!m.exportFormat;
                const isEditing = editingIdx === idx;
                return (
                  <div key={idx} className={`msg-row ${m.role}`}>
                    <div className="msg-card">
                      {m.attachments && m.attachments.length > 0 && (
                        <div className="sent-attach-row">
                          {m.attachments.map((a, aIdx) => (
                            <span
                              key={aIdx}
                              className="sent-attach-pill"
                              onClick={() => a.view_url && openPdfAtPage(a.name, a.view_url, 1)}
                            >
                              {a.type === "image" ? "IMG" : "PDF"}: {a.name}
                            </span>
                          ))}
                        </div>
                      )}

                      {isEditing ? (
                        <>
                          <textarea
                            className="edit-box"
                            autoFocus
                            value={editText}
                            onChange={(e) => setEditText(e.target.value)}
                            onFocus={(e) => {
                              const len = e.target.value.length;
                              e.target.setSelectionRange(len, len);
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Escape") {
                                e.preventDefault();
                                cancelEdit();
                              } else if (e.key === "Enter" && !e.shiftKey) {
                                e.preventDefault();
                                submitEdit();
                              }
                            }}
                          />
                          <div className="msg-actions" style={{ marginTop: 8 }}>
                            <button className="action-btn" onClick={submitEdit} disabled={!editText.trim() || asking}>
                              Save and resend
                            </button>
                            <button className="action-btn" onClick={cancelEdit}>
                              Cancel
                            </button>
                          </div>
                          <div className="edit-hint">Enter to resend, Shift+Enter for a new line, Esc to cancel</div>
                        </>
                      ) : (
                        <>
                          <div className="msg-body" style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                          <div className="msg-actions">
                            {showExportBtn && (
                              <button className="action-btn" onClick={() => downloadExtract(m)}>
                                <IconDown /> Download as {m.exportFormat.toUpperCase()}
                              </button>
                            )}
                            {m.role === "assistant" && m.citations?.length > 0 && (
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
                            <span className="hover-actions">
                              <button className="action-btn" onClick={() => copyText(m.exportAnswer || m.content, `m${idx}`)}>
                                {copiedKey === `m${idx}` ? "Copied" : "Copy"}
                              </button>
                              {m.role === "user" && (
                                <button className="action-btn" onClick={() => startEdit(idx)} disabled={asking}>
                                  Edit
                                </button>
                              )}
                            </span>
                          </div>
                        </>
                      )}

                      {m.citations && m.citations.length > 0 && !isEditing && (
                        <div className="cite-strip">
                          {m.citations.map((c, cIdx) => (
                            <button
                              key={cIdx}
                              className="cite-pill"
                              onClick={() => openPdfAtPage(c.doc_name, c.view_url, c.page_number)}
                            >
                              {c.doc_name} (p.{c.page_number})
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {asking && (
                <div className="msg-row assistant">
                  <div className="msg-card muted" style={{ width: "fit-content" }}>
                    <div className="typing-dots" role="status" aria-label="Thinking">
                      <span />
                      <span />
                      <span />
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="bottom-dock">
              {renderPendingChips()}
              {renderScopePrompt()}
              <form
                className="bottom-composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  sendQuestion();
                }}
              >
                <label className="pill-btn" title="Attach PDF or Invoice Photo to this chat only">
                  + Attach
                  <input
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                    multiple
                    disabled={uploading}
                    onChange={(e) => handleUpload(e, "chat")}
                    hidden
                  />
                </label>
                <textarea
                  ref={followUpRef}
                  rows={1}
                  placeholder="Ask a follow-up question..."
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      sendQuestion();
                    }
                  }}
                  disabled={asking}
                  style={{
                    flex: 1,
                    resize: "none",
                    background: "transparent",
                    color: "inherit",
                    border: "none",
                    outline: "none",
                    font: "inherit",
                    lineHeight: 1.4,
                    maxHeight: 140,
                    padding: "8px 4px",
                  }}
                />
                <button className="send-circle" type="submit" disabled={asking || scopePromptOpen || !question.trim()}>
                  <IconUp />
                </button>
              </form>
            </div>
          </>
        )}
      </main>

      {inspectorOpen && (
        <aside className="inspector">
          <div className="insp-tabs">
            <button className={inspectorTab === "pdf" ? "active" : ""} onClick={() => setInspectorTab("pdf")}>
              Document Viewer
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
                    <a href={activePdf.url} target="_blank" rel="noreferrer">Open</a>
                  </div>
                  <iframe key={activePdf.url} src={activePdf.url} title="Document Preview" className="pdf-frame" />
                </>
              ) : (
                <div className="insp-empty">Click any document or citation to view it here.</div>
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
                      <strong>{c.doc_name} (Page {c.page_number})</strong>
                      <button onClick={() => openPdfAtPage(c.doc_name, c.view_url, c.page_number)}>
                        Jump to p.{c.page_number}
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