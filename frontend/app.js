// ── State ───────────────────────────────────────────────────────────────────
const API = "";
let conversationHistory = []; // [{role, content, attachments?}, ...]
let isStreaming = false;
let currentConfig = {};
let currentChatId = null;
let pendingAttachments = []; // staged for next send
let forceSearchMode = false; // 🔍 toggle

// ── DOM Refs ─────────────────────────────────────────────────────────────────
const messagesEl = document.getElementById("messages");
const userInput = document.getElementById("user-input");
const btnSend = document.getElementById("btn-send");
const btnSearch = document.getElementById("btn-search");
const btnAttach = document.getElementById("btn-attach");
const btnImage = document.getElementById("btn-image");
const attachmentsBar = document.getElementById("attachments-bar");
const btnNewChat = document.getElementById("btn-new-chat");
const btnSettings = document.getElementById("btn-settings");
const btnSidebarToggle = document.getElementById("btn-sidebar-toggle");
const btnSidebarClose = document.getElementById("btn-sidebar-close");
const sidebar = document.getElementById("sidebar");
const chatListEl = document.getElementById("chat-list");
const modelBadge = document.getElementById("model-badge");
const searchIndicator = document.getElementById("search-indicator");
const searchLabel = document.getElementById("search-label");

const btnNotesToggle = document.getElementById("btn-notes-toggle");
const btnNotesClose = document.getElementById("btn-notes-close");
const notesSidebar = document.getElementById("notes-sidebar");
const notesSearch = document.getElementById("notes-search");
const notesListEl = document.getElementById("notes-list");
const notesDetailOverlay = document.getElementById("notes-detail-overlay");
const notesDetailBody = document.getElementById("notes-detail-body");
const notesDetailClose = document.getElementById("notes-detail-close");
const notesDetailDelete = document.getElementById("notes-detail-delete");
let currentNoteId = null;

// ── Init ─────────────────────────────────────────────────────────────────────
async function init() {
  await loadConfig();
  modelBadge.textContent = currentConfig.ollama_model || "—";

  // Configure marked
  marked.setOptions({
    breaks: true,
    gfm: true,
    highlight: function (code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    },
  });

  await loadChatList();
  probeComfyuiOnce();
}

// ── Config ───────────────────────────────────────────────────────────────────
async function loadConfig() {
  try {
    const r = await fetch(`${API}/api/config`);
    currentConfig = await r.json();
  } catch (e) {
    console.error("Could not load config:", e);
  }
}

async function saveConfig(data) {
  await fetch(`${API}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  currentConfig = { ...currentConfig, ...data };
  modelBadge.textContent = currentConfig.ollama_model || "—";
}

// ── Send Message ─────────────────────────────────────────────────────────────
async function sendMessage(forceSearch = false) {
  const text = userInput.value.trim();
  if ((!text && !pendingAttachments.length) || isStreaming) return;

  const validAttachments = pendingAttachments.filter((a) => !a.error);
  const userMsg = { role: "user", content: text };
  if (validAttachments.length) userMsg.attachments = validAttachments;

  conversationHistory.push(userMsg);
  appendMessage("user", text, validAttachments);
  userInput.value = "";
  pendingAttachments = [];
  renderAttachmentsBar();
  autoResizeTextarea();
  scrollToBottom();

  await streamResponse(forceSearch);
}

async function streamResponse(forceSearch = false) {
  setInputLocked(true);
  const assistantBubble = createAssistantBubble();
  let buffer = "";
  let sources = [];
  let sseLineBuffer = "";

  try {
    const response = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: conversationHistory,
        force_search: forceSearch,
      }),
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // {stream: true} keeps partial multi-byte UTF-8 chars between reads
      sseLineBuffer += decoder.decode(value, { stream: true });
      const lines = sseLineBuffer.split("\n");
      // last element may be incomplete — hold it for next iteration
      sseLineBuffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch (e) {
          continue;
        }

        if (event.type === "searching") {
          showIndicator("Searching…");
        } else if (event.type === "loading_notebook") {
          showIndicator("Loading notebook…");
        } else if (event.type === "fetching_urls") {
          const n = (event.urls || []).length;
          showIndicator(n === 1 ? "Reading page…" : `Reading ${n} pages…`);
        } else if (event.type === "remembered") {
          showIndicator(`✓ Saved to memory`);
          setTimeout(hideIndicator, 1500);
        } else if (event.type === "sources") {
          sources = event.sources || [];
        } else if (event.type === "token") {
          hideIndicator();
          buffer += event.content;
          assistantBubble.innerHTML = escapeHtml(buffer);
          assistantBubble.classList.add("streaming-cursor");
          scrollToBottom();
        } else if (event.type === "done") {
          assistantBubble.innerHTML = marked.parse(buffer);
          assistantBubble.classList.remove("streaming-cursor");
          assistantBubble
            .querySelectorAll("pre code")
            .forEach((el) => hljs.highlightElement(el));
          conversationHistory.push({
            role: "assistant",
            content: buffer,
            sources,
          });
          const msgEl = assistantBubble.closest(".msg");
          if (sources.length) renderSources(msgEl, sources);
          attachMessageActions(msgEl);
          scrollToBottom();
          await saveCurrentChat();
        } else if (event.type === "error") {
          assistantBubble.innerHTML = `⚠ ${escapeHtml(event.message)}`;
          assistantBubble.closest(".msg").classList.add("error");
        }
      }
    }
  } catch (e) {
    assistantBubble.innerHTML = `⚠ Connection error: ${escapeHtml(e.message)}`;
    assistantBubble.closest(".msg").classList.add("error");
  } finally {
    hideIndicator();
    setInputLocked(false);
    userInput.focus();
  }
}

// ── Sources & Per-Message Actions ────────────────────────────────────────────
function renderSources(msgEl, sources) {
  const wrap = document.createElement("div");
  wrap.className = "msg-sources";
  const label = document.createElement("div");
  label.className = "msg-sources-label";
  label.textContent = "Sources";
  const ol = document.createElement("ol");
  sources.forEach((s, i) => {
    const li = document.createElement("li");
    const num = document.createElement("span");
    num.className = "src-num";
    num.textContent = `[${i + 1}]`;
    const a = document.createElement("a");
    a.href = s.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = s.title || s.url;
    li.append(num, a);
    ol.appendChild(li);
  });
  wrap.append(label, ol);
  msgEl.appendChild(wrap);
}

function attachMessageActions(msgEl) {
  if (msgEl.querySelector(".msg-actions")) return;
  const bar = document.createElement("div");
  bar.className = "msg-actions";

  const copyBtn = document.createElement("button");
  copyBtn.className = "msg-action-btn";
  copyBtn.textContent = "⎘ Copy";
  copyBtn.onclick = async () => {
    const content = getAssistantContentFor(msgEl);
    try {
      await navigator.clipboard.writeText(content);
      copyBtn.textContent = "✓ Copied";
      copyBtn.classList.add("flash");
      setTimeout(() => {
        copyBtn.textContent = "⎘ Copy";
        copyBtn.classList.remove("flash");
      }, 1200);
    } catch (e) {
      console.error("Copy failed:", e);
    }
  };

  const regenBtn = document.createElement("button");
  regenBtn.className = "msg-action-btn";
  regenBtn.textContent = "↻ Regenerate";
  regenBtn.onclick = () => regenerateFromMessage(msgEl);

  const noteBtn = document.createElement("button");
  noteBtn.className = "msg-action-btn";
  noteBtn.textContent = "📝 Note";
  noteBtn.title = "Save this answer as a note";
  noteBtn.onclick = async () => {
    const content = getAssistantContentFor(msgEl);
    const question = getQuestionFor(msgEl);
    const sources = getSourcesFor(msgEl);
    await takeNote(question, content, sources, noteBtn);
  };

  bar.append(copyBtn, regenBtn, noteBtn);
  msgEl.appendChild(bar);
}

function getAssistantContentFor(msgEl) {
  const all = [...messagesEl.querySelectorAll(".msg.assistant")];
  const idx = all.indexOf(msgEl);
  let count = -1;
  for (const m of conversationHistory) {
    if (m.role === "assistant") {
      count++;
      if (count === idx) return m.content || "";
    }
  }
  return "";
}

function getQuestionFor(msgEl) {
  // Find the user message immediately before this assistant message
  const all = [...messagesEl.querySelectorAll(".msg.assistant")];
  const idx = all.indexOf(msgEl);
  let assistantCount = -1;
  for (let i = 0; i < conversationHistory.length; i++) {
    if (conversationHistory[i].role === "assistant") {
      assistantCount++;
      if (assistantCount === idx) {
        // Walk backwards to find the preceding user message
        for (let j = i - 1; j >= 0; j--) {
          if (conversationHistory[j].role === "user") {
            return conversationHistory[j].content || "";
          }
        }
        return "";
      }
    }
  }
  return "";
}

function getSourcesFor(msgEl) {
  const all = [...messagesEl.querySelectorAll(".msg.assistant")];
  const idx = all.indexOf(msgEl);
  let assistantCount = -1;
  for (let i = 0; i < conversationHistory.length; i++) {
    if (conversationHistory[i].role === "assistant") {
      assistantCount++;
      if (assistantCount === idx) {
        return conversationHistory[i].sources || [];
      }
    }
  }
  return [];
}

async function takeNote(question, answer, sources, btnEl) {
  if (!question || !answer) return;
  try {
    const resp = await fetch(`${API}/api/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        answer,
        sources: sources || [],
        chat_id: currentChatId || "",
        model: currentConfig.ollama_model || "",
      }),
    });
    if (resp.ok) {
      btnEl.textContent = "✓ Noted";
      btnEl.classList.add("flash");
      setTimeout(() => {
        btnEl.textContent = "📝 Note";
        btnEl.classList.remove("flash");
      }, 1200);
      // Refresh notes sidebar if open
      if (notesSidebar.classList.contains("open")) {
        loadNotes(notesSearch.value.trim() || null);
      }
    } else {
      btnEl.textContent = "✗ Failed";
      setTimeout(() => {
        btnEl.textContent = "📝 Note";
      }, 1200);
    }
  } catch (e) {
    console.error("Take note failed:", e);
    btnEl.textContent = "✗ Failed";
    setTimeout(() => {
      btnEl.textContent = "📝 Note";
    }, 1200);
  }
}

async function regenerateFromMessage(msgEl) {
  if (isStreaming) return;
  const all = [...messagesEl.querySelectorAll(".msg.assistant")];
  const targetIdx = all.indexOf(msgEl);
  if (targetIdx === -1) return;

  // find corresponding index in conversationHistory
  let count = -1,
    historyIdx = -1;
  for (let i = 0; i < conversationHistory.length; i++) {
    if (conversationHistory[i].role === "assistant") {
      count++;
      if (count === targetIdx) {
        historyIdx = i;
        break;
      }
    }
  }
  if (historyIdx === -1) return;

  // truncate history at (and including) this assistant
  conversationHistory = conversationHistory.slice(0, historyIdx);

  // remove this assistant bubble + everything after it from DOM
  let el = msgEl;
  while (el) {
    const next = el.nextElementSibling;
    el.remove();
    el = next;
  }

  await streamResponse(false);
}

// ── UI Helpers ───────────────────────────────────────────────────────────────
function appendMessage(role, content, attachments) {
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;

  const roleLabel = document.createElement("div");
  roleLabel.className = "msg-role";
  roleLabel.textContent = role === "user" ? "you" : "assistant";

  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = content;

  msg.appendChild(roleLabel);
  msg.appendChild(body);
  if (attachments && attachments.length) {
    msg.appendChild(renderMessageAttachments(attachments));
  }
  messagesEl.appendChild(msg);
  return body;
}

function renderMessageAttachments(attachments) {
  const wrap = document.createElement("div");
  wrap.className = "msg-attachments";
  for (const a of attachments) {
    const chip = document.createElement("span");
    chip.className = "msg-attach-chip";
    chip.textContent = `${categoryIcon(a.category)} ${a.filename}`;
    chip.title = `${a.category} · ${formatBytes(a.size || 0)}`;
    wrap.appendChild(chip);
  }
  return wrap;
}

function categoryIcon(category) {
  return { image: "🖼", audio: "🎵", video: "🎞" }[category] || "📄";
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function createAssistantBubble() {
  const msg = document.createElement("div");
  msg.className = "msg assistant";

  const roleLabel = document.createElement("div");
  roleLabel.className = "msg-role";
  roleLabel.textContent = "assistant";

  const body = document.createElement("div");
  body.className = "msg-body";

  msg.appendChild(roleLabel);
  msg.appendChild(body);
  messagesEl.appendChild(msg);
  return body;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function scrollToBottom() {
  const chatArea = document.getElementById("chat-area");
  chatArea.scrollTop = chatArea.scrollHeight;
}

function setInputLocked(locked) {
  isStreaming = locked;
  btnSend.disabled = locked;
  userInput.disabled = locked;
  btnSearch.disabled = locked;
}

function showIndicator(label) {
  searchLabel.textContent = label || "Working…";
  searchIndicator.classList.remove("hidden");
  scrollToBottom();
}

function hideIndicator() {
  searchIndicator.classList.add("hidden");
}

function autoResizeTextarea() {
  userInput.style.height = "auto";
  userInput.style.height = Math.min(userInput.scrollHeight, 160) + "px";
}

// ── Attachments ──────────────────────────────────────────────────────────────
async function pickAttachments() {
  if (isStreaming) return;
  if (
    !window.pywebview ||
    !window.pywebview.api ||
    !window.pywebview.api.pick_files
  ) {
    alert("File picker requires the desktop app (pywebview not available).");
    return;
  }
  try {
    const result = await window.pywebview.api.pick_files();
    if (!Array.isArray(result) || !result.length) return;
    for (const f of result) pendingAttachments.push(f);
    renderAttachmentsBar();
  } catch (e) {
    console.error("Pick failed:", e);
  }
}

function renderAttachmentsBar() {
  attachmentsBar.innerHTML = "";
  if (!pendingAttachments.length) {
    attachmentsBar.classList.add("hidden");
    return;
  }
  attachmentsBar.classList.remove("hidden");
  pendingAttachments.forEach((a, i) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip" + (a.error ? " error" : "");

    const icon = document.createElement("span");
    icon.className = "attach-chip-icon";
    icon.textContent = categoryIcon(a.category);

    const name = document.createElement("span");
    name.className = "attach-chip-name";
    name.textContent = a.filename;
    name.title = a.error
      ? a.error
      : `${a.category} · ${formatBytes(a.size || 0)}`;

    const meta = document.createElement("span");
    meta.className = "attach-chip-meta";
    meta.textContent = a.error ? "✗" : formatBytes(a.size || 0);

    const rm = document.createElement("button");
    rm.className = "attach-chip-remove";
    rm.textContent = "✕";
    rm.title = "Remove";
    rm.onclick = () => {
      pendingAttachments.splice(i, 1);
      renderAttachmentsBar();
    };

    chip.append(icon, name, meta, rm);
    attachmentsBar.appendChild(chip);
  });
}

// ── New Chat ─────────────────────────────────────────────────────────────────
function newChat() {
  conversationHistory = [];
  currentChatId = null;
  messagesEl.innerHTML = "";
  userInput.value = "";
  pendingAttachments = [];
  renderAttachmentsBar();
  userInput.focus();
  highlightActiveChat();
}

// ── Chat Persistence ─────────────────────────────────────────────────────────
async function saveCurrentChat() {
  if (!conversationHistory.length) return;
  if (!currentChatId) currentChatId = crypto.randomUUID();
  try {
    await fetch(`${API}/api/chats/${currentChatId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: conversationHistory }),
    });
    await loadChatList();
  } catch (e) {
    console.error("Save failed:", e);
  }
}

async function loadChatList() {
  try {
    const r = await fetch(`${API}/api/chats`);
    const data = await r.json();
    renderChatList(data.chats || []);
  } catch (e) {
    console.error("Load list failed:", e);
  }
}

function groupChatsByDate(chats) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const weekAgo = new Date(today);
  weekAgo.setDate(today.getDate() - 7);

  const groups = { Today: [], Yesterday: [], "Last 7 days": [], Older: [] };
  for (const c of chats) {
    const d = new Date(c.updated_at);
    if (d >= today) groups.Today.push(c);
    else if (d >= yesterday) groups.Yesterday.push(c);
    else if (d >= weekAgo) groups["Last 7 days"].push(c);
    else groups.Older.push(c);
  }
  return groups;
}

function renderChatList(chats) {
  chatListEl.innerHTML = "";
  if (!chats.length) {
    const empty = document.createElement("div");
    empty.className = "chat-list-empty";
    empty.textContent = "No chats yet";
    chatListEl.appendChild(empty);
    return;
  }
  const groups = groupChatsByDate(chats);
  for (const [label, items] of Object.entries(groups)) {
    if (!items.length) continue;
    const labelEl = document.createElement("div");
    labelEl.className = "chat-group-label";
    labelEl.textContent = label;
    chatListEl.appendChild(labelEl);
    for (const c of items) chatListEl.appendChild(renderChatEntry(c));
  }
}

function renderChatEntry(chat) {
  const entry = document.createElement("div");
  entry.className = "chat-entry";
  entry.dataset.chatId = chat.id;
  if (chat.id === currentChatId) entry.classList.add("active");

  const title = document.createElement("div");
  title.className = "chat-entry-title";
  title.textContent = chat.title || "Untitled";
  title.title = chat.title || "Untitled";

  const actions = document.createElement("div");
  actions.className = "chat-entry-actions";

  const renameBtn = document.createElement("button");
  renameBtn.textContent = "✎";
  renameBtn.title = "Rename";
  renameBtn.onclick = (e) => {
    e.stopPropagation();
    renameChat(chat.id, chat.title);
  };

  const exportBtn = document.createElement("button");
  exportBtn.textContent = "↓";
  exportBtn.title = "Export JSON";
  exportBtn.onclick = (e) => {
    e.stopPropagation();
    exportChat(chat.id);
  };

  const delBtn = document.createElement("button");
  delBtn.textContent = "✕";
  delBtn.title = "Delete";
  delBtn.onclick = (e) => {
    e.stopPropagation();
    deleteChat(chat.id, chat.title);
  };

  actions.append(renameBtn, exportBtn, delBtn);
  entry.append(title, actions);
  entry.onclick = () => loadChat(chat.id);
  return entry;
}

function highlightActiveChat() {
  for (const el of chatListEl.querySelectorAll(".chat-entry")) {
    el.classList.toggle("active", el.dataset.chatId === currentChatId);
  }
}

async function loadChat(chatId) {
  if (isStreaming) return;
  try {
    const r = await fetch(`${API}/api/chats/${chatId}`);
    if (!r.ok) return;
    const data = await r.json();
    currentChatId = data.id;
    conversationHistory = data.messages || [];
    messagesEl.innerHTML = "";
    for (const m of conversationHistory) {
      if (m.role === "user") {
        appendMessage("user", m.content, m.attachments);
      } else if (m.role === "assistant") {
        if (m.image) {
          renderAssistantImage(m);
          continue;
        }
        const bubble = createAssistantBubble();
        bubble.innerHTML = marked.parse(m.content || "");
        bubble
          .querySelectorAll("pre code")
          .forEach((el) => hljs.highlightElement(el));
        const msgEl = bubble.closest(".msg");
        if (m.sources && m.sources.length) renderSources(msgEl, m.sources);
        attachMessageActions(msgEl);
      }
    }
    scrollToBottom();
    highlightActiveChat();
  } catch (e) {
    console.error("Load chat failed:", e);
  }
}

async function renameChat(chatId, oldTitle) {
  const title = prompt("New title:", oldTitle || "");
  if (title === null) return;
  const trimmed = title.trim();
  if (!trimmed) return;
  try {
    await fetch(`${API}/api/chats/${chatId}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: trimmed }),
    });
    await loadChatList();
  } catch (e) {
    console.error("Rename failed:", e);
  }
}

async function exportChat(chatId) {
  // Native save dialog via pywebview js_api (works inside the desktop app)
  if (
    window.pywebview &&
    window.pywebview.api &&
    window.pywebview.api.export_chat
  ) {
    try {
      const result = await window.pywebview.api.export_chat(chatId);
      if (result && result.ok === false && !result.cancelled) {
        alert("Export failed: " + (result.error || "unknown"));
      }
    } catch (e) {
      console.error("Export failed:", e);
      alert("Export failed: " + e.message);
    }
    return;
  }
  // Fallback: browser blob download (for plain-browser dev mode)
  try {
    const r = await fetch(`${API}/api/chats/${chatId}`);
    const data = await r.json();
    const safeTitle = (data.title || "chat")
      .replace(/[^\w\-]+/g, "_")
      .slice(0, 60);
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeTitle}_${data.id.slice(0, 8)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error("Export failed:", e);
  }
}

async function deleteChat(chatId, title) {
  if (!confirm(`Delete "${title || "this chat"}"?`)) return;
  try {
    await fetch(`${API}/api/chats/${chatId}`, { method: "DELETE" });
    if (currentChatId === chatId) newChat();
    await loadChatList();
  } catch (e) {
    console.error("Delete failed:", e);
  }
}

function toggleSidebar() {
  sidebar.classList.toggle("open");
  if (sidebar.classList.contains("open")) loadChatList();
}

// ── Notes Sidebar (Phase C1-C4) ────────────────────────────────────────────
function toggleNotesSidebar() {
  notesSidebar.classList.toggle("open");
  if (notesSidebar.classList.contains("open")) {
    loadNotes();
    notesSearch.value = "";
    notesSearch.focus();
  }
}

function closeNotesSidebar() {
  notesSidebar.classList.remove("open");
}

async function loadNotes(query) {
  try {
    const url = query
      ? `${API}/api/notes?q=${encodeURIComponent(query)}`
      : `${API}/api/notes`;
    const r = await fetch(url);
    const notes = await r.json();
    renderNotesList(notes, query);
  } catch (e) {
    console.error("Load notes failed:", e);
    notesListEl.innerHTML =
      '\u003cdiv class="chat-list-empty"\u003eFailed to load notes\u003c/div\u003e';
  }
}

function renderNotesList(notes, query) {
  notesListEl.innerHTML = "";
  if (!notes || !notes.length) {
    const empty = document.createElement("div");
    empty.className = "chat-list-empty";
    empty.textContent = query
      ? `No notes match "${query}"`
      : "No notes yet. Click 📝 Note under any answer to save it.";
    notesListEl.appendChild(empty);
    return;
  }
  for (const n of notes) {
    notesListEl.appendChild(renderNoteEntry(n));
  }
}

function renderNoteEntry(note) {
  const entry = document.createElement("div");
  entry.className = "note-entry";
  entry.onclick = () => openNoteDetail(note.id);

  const title = document.createElement("div");
  title.className = "note-entry-title";
  title.textContent = note.snippet || note.question?.slice(0, 140) || "(empty)";

  const meta = document.createElement("div");
  meta.className = "note-entry-meta";
  const d = new Date(note.created_at);
  const dateStr = d.toLocaleString();
  const modelStr = note.model ? ` · ${note.model}` : "";
  meta.textContent = `${dateStr}${modelStr}`;

  entry.append(title, meta);
  return entry;
}

async function openNoteDetail(noteId) {
  try {
    const r = await fetch(`${API}/api/notes/${encodeURIComponent(noteId)}`);
    if (!r.ok) return;
    const note = await r.json();
    renderNoteDetail(note);
    currentNoteId = note.id;
    notesDetailOverlay.classList.remove("hidden");
  } catch (e) {
    console.error("Open note detail failed:", e);
  }
}

function renderNoteDetail(note) {
  const d = new Date(note.created_at);
  const dateStr = d.toLocaleString();
  const modelStr = note.model || "unknown";
  const chatStr = note.chat_id?.slice(0, 8) || "—";

  let html = "";
  html += `\u003cdiv class="note-meta-line"\u003e${dateStr} · ${modelStr} · Chat: ${escapeHtml(chatStr)}\u003c/div\u003e`;

  html += '\u003cdiv class="note-section"\u003e';
  html += '\u003cdiv class="note-section-label"\u003eQuestion\u003c/div\u003e';
  html += `\u003cdiv class="note-section-content"\u003e${marked.parse(escapeHtml(note.question || ""))}\u003c/div\u003e`;
  html += "\u003c/div\u003e";

  html += '\u003cdiv class="note-section"\u003e';
  html += '\u003cdiv class="note-section-label"\u003eAnswer\u003c/div\u003e';
  html += `\u003cdiv class="note-section-content"\u003e${marked.parse(note.answer || "")}\u003c/div\u003e`;
  html += "\u003c/div\u003e";

  const sources = note.sources || [];
  if (sources.length) {
    html += '\u003cdiv class="note-section"\u003e';
    html += '\u003cdiv class="note-section-label"\u003eSources\u003c/div\u003e';
    html += '\u003cdiv class="note-sources"\u003e';
    sources.forEach((s, i) => {
      html += `\u003ca href="${escapeHtml(s.url || "")}" target="_blank" rel="noopener noreferrer"\u003e[${i + 1}] ${escapeHtml(s.title || s.url || "")}\u003c/a\u003e`;
    });
    html += "\u003c/div\u003e\u003c/div\u003e";
  }

  notesDetailBody.innerHTML = html;
}

function closeNoteDetail() {
  notesDetailOverlay.classList.add("hidden");
  currentNoteId = null;
}

async function deleteCurrentNote() {
  if (!currentNoteId) return;
  if (!confirm("Delete this note? This cannot be undone.")) return;
  try {
    const r = await fetch(
      `${API}/api/notes/${encodeURIComponent(currentNoteId)}`,
      {
        method: "DELETE",
      },
    );
    if (r.ok) {
      closeNoteDetail();
      loadNotes(notesSearch.value.trim() || null);
    }
  } catch (e) {
    console.error("Delete note failed:", e);
  }
}

// ── Settings ─────────────────────────────────────────────────────────────────
async function openSettings() {
  await loadConfig();
  const c = currentConfig;

  document.getElementById("s-ollama-host").value = c.ollama_host || "";
  document.getElementById("s-search-provider").value =
    c.search_provider || "ddg";
  document.getElementById("s-brave-api-key").value = c.brave_api_key || "";
  document.getElementById("s-searxng-url").value = c.searxng_url || "";
  document.getElementById("s-results-count").value =
    c.search_results_count || c.searxng_results_count || 5;
  document.getElementById("s-auto-search").checked = !!c.auto_search_enabled;
  document.getElementById("s-user-location").value = c.user_location || "";
  document.getElementById("s-notebook-url").value = c.open_notebook_url || "";
  document.getElementById("s-notebook-ui-url").value =
    c.open_notebook_ui_url || "";

  // ComfyUI fields
  document.getElementById("s-comfyui-url").value = c.comfyui_url || "";
  document.getElementById("s-comfyui-workflow").value =
    c.comfyui_workflow || "sdxl-default";
  document.getElementById("s-comfyui-custom-workflow").value =
    c.comfyui_custom_workflow || "";
  document.getElementById("s-comfyui-steps").value = c.comfyui_steps ?? 30;
  document.getElementById("s-comfyui-cfg").value = c.comfyui_cfg ?? 7.5;
  document.getElementById("s-comfyui-width").value = c.comfyui_width ?? 1024;
  document.getElementById("s-comfyui-height").value = c.comfyui_height ?? 1024;
  document.getElementById("s-comfyui-negative").value =
    c.comfyui_negative_prompt || "";
  document.getElementById("s-comfyui-commentary").checked =
    !!c.comfyui_post_commentary;
  updateComfyuiCustomVisibility();

  updateProviderFieldsVisibility();
  await refreshNotebooks(c.active_notebook_id || "");
  await refreshComfyui(c);
  document.getElementById("s-system-prompt").value = c.system_prompt || "";
  document.getElementById("s-temperature").value = c.temperature || 0.7;
  document.getElementById("s-temperature-val").textContent =
    c.temperature || 0.7;

  // Per-model override state
  updateTempOverrideUI();
  document.getElementById("s-conservative-mode").checked = !!(
    c.model_overrides &&
    c.model_overrides[c.ollama_model] &&
    c.model_overrides[c.ollama_model].temperature !== undefined
  );

  // Load models
  await refreshModels(c.ollama_model);

  // Load memory
  await loadMemory();

  // Load version
  fetch("/api/version")
    .then((r) => r.json())
    .then((data) => {
      document.getElementById("s-version").textContent =
        "Logos v" + data.version;
    })
    .catch(() => {
      document.getElementById("s-version").textContent = "Logos";
    });

  restoreSettingsTab();
  document.getElementById("settings-overlay").classList.remove("hidden");
}

async function loadMemory() {
  const listEl = document.getElementById("s-memory-list");
  listEl.innerHTML = "";
  try {
    const r = await fetch(`${API}/api/memory`);
    const data = await r.json();
    const facts = data.facts || [];
    if (!facts.length) {
      const empty = document.createElement("div");
      empty.className = "memory-empty";
      empty.textContent = "No memories yet";
      listEl.appendChild(empty);
      return;
    }
    for (const f of facts) listEl.appendChild(renderMemoryItem(f));
  } catch (e) {
    console.error("Load memory failed:", e);
  }
}

function renderMemoryItem(fact) {
  const item = document.createElement("div");
  item.className = "memory-item";

  const main = document.createElement("div");
  main.style.flex = "1";

  const text = document.createElement("div");
  text.className = "memory-item-text";
  text.textContent = fact.text;

  const meta = document.createElement("div");
  meta.className = "memory-item-meta";
  meta.textContent = `${fact.source || "auto"} · ${(fact.created_at || "").slice(0, 10)}`;

  main.append(text, meta);

  const del = document.createElement("button");
  del.className = "memory-item-delete";
  del.textContent = "✕";
  del.title = "Delete";
  del.onclick = async () => {
    if (!confirm(`Delete: "${fact.text}"?`)) return;
    await fetch(`${API}/api/memory/${fact.id}`, { method: "DELETE" });
    await loadMemory();
  };

  item.append(main, del);
  return item;
}

async function refreshModels(currentModel) {
  try {
    const r = await fetch(`${API}/api/models`);
    const data = await r.json();
    const sel = document.getElementById("s-model");
    sel.innerHTML = "";
    for (const m of data.models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (m === currentModel) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch (e) {
    console.error("Could not fetch models:", e);
  }
}

function closeSettings() {
  document.getElementById("settings-overlay").classList.add("hidden");
}

async function saveSettings() {
  const temp = parseFloat(document.getElementById("s-temperature").value);
  await saveConfig({
    ollama_host: document.getElementById("s-ollama-host").value.trim(),
    ollama_model: document.getElementById("s-model").value,
    search_provider: document.getElementById("s-search-provider").value,
    brave_api_key: document.getElementById("s-brave-api-key").value.trim(),
    searxng_url: document.getElementById("s-searxng-url").value.trim(),
    search_results_count: parseInt(
      document.getElementById("s-results-count").value,
    ),
    auto_search_enabled: document.getElementById("s-auto-search").checked,
    user_location: document.getElementById("s-user-location").value.trim(),
    open_notebook_url: document.getElementById("s-notebook-url").value.trim(),
    open_notebook_ui_url: document
      .getElementById("s-notebook-ui-url")
      .value.trim(),
    active_notebook_id: document.getElementById("s-notebook-active").value,
    active_notebook_name:
      document.getElementById("s-notebook-active").options[
        document.getElementById("s-notebook-active").selectedIndex
      ]?.dataset.name || "",
    comfyui_url: document.getElementById("s-comfyui-url").value.trim(),
    comfyui_workflow: document.getElementById("s-comfyui-workflow").value,
    comfyui_custom_workflow: document.getElementById(
      "s-comfyui-custom-workflow",
    ).value,
    comfyui_checkpoint: document.getElementById("s-comfyui-checkpoint").value,
    comfyui_sampler: document.getElementById("s-comfyui-sampler").value,
    comfyui_scheduler: document.getElementById("s-comfyui-scheduler").value,
    comfyui_steps:
      parseInt(document.getElementById("s-comfyui-steps").value) || 30,
    comfyui_cfg:
      parseFloat(document.getElementById("s-comfyui-cfg").value) || 7.5,
    comfyui_width:
      parseInt(document.getElementById("s-comfyui-width").value) || 1024,
    comfyui_height:
      parseInt(document.getElementById("s-comfyui-height").value) || 1024,
    comfyui_negative_prompt:
      document.getElementById("s-comfyui-negative").value,
    comfyui_post_commentary: document.getElementById("s-comfyui-commentary")
      .checked,
    system_prompt: document.getElementById("s-system-prompt").value,
    temperature: hasTempOverride() ? currentConfig.temperature : temp,
    model_overrides: currentConfig.model_overrides || {},
  });
  closeSettings();
}

// ── Event Listeners ───────────────────────────────────────────────────────────
btnSend.addEventListener("click", () => sendMessage(forceSearchMode));
btnSearch.addEventListener("click", toggleForceSearch);
btnAttach.addEventListener("click", pickAttachments);
btnImage.addEventListener("click", openImageModal);

document
  .getElementById("image-close")
  .addEventListener("click", closeImageModal);
document
  .getElementById("img-cancel")
  .addEventListener("click", closeImageModal);
document
  .getElementById("img-generate")
  .addEventListener("click", generateImage);

document
  .getElementById("s-comfyui-workflow")
  .addEventListener("change", updateComfyuiCustomVisibility);
document
  .getElementById("s-comfyui-connect")
  .addEventListener("click", async () => {
    await saveConfig({
      comfyui_url: document.getElementById("s-comfyui-url").value.trim(),
    });
    await refreshComfyui(currentConfig, { force: true });
    await probeComfyuiOnce();
  });

function toggleForceSearch() {
  forceSearchMode = !forceSearchMode;
  btnSearch.classList.toggle("active", forceSearchMode);
  btnSearch.title = forceSearchMode
    ? "Force web search: ON (click to disable)"
    : "Force web search: OFF (click to enable)";
}
btnNewChat.addEventListener("click", newChat);
btnSettings.addEventListener("click", openSettings);
btnSidebarToggle.addEventListener("click", toggleSidebar);
btnSidebarClose.addEventListener("click", toggleSidebar);

document
  .getElementById("settings-close")
  .addEventListener("click", closeSettings);
document
  .getElementById("settings-cancel")
  .addEventListener("click", closeSettings);
document
  .getElementById("settings-save")
  .addEventListener("click", saveSettings);

document.getElementById("s-refresh-models").addEventListener("click", () => {
  refreshModels(document.getElementById("s-model").value);
});

document
  .getElementById("s-memory-refresh")
  .addEventListener("click", loadMemory);

document
  .getElementById("s-search-provider")
  .addEventListener("change", updateProviderFieldsVisibility);

// ── Notebook (Open Notebook) ─────────────────────────────────────────────
document
  .getElementById("s-notebook-connect")
  .addEventListener("click", async () => {
    // Persist URL before testing
    const url = document.getElementById("s-notebook-url").value.trim();
    await saveConfig({ open_notebook_url: url });
    await refreshNotebooks(document.getElementById("s-notebook-active").value);
  });

document
  .getElementById("s-notebook-refresh")
  .addEventListener("click", async () => {
    await fetch(`${API}/api/notebooks/refresh`, { method: "POST" });
    const id = document.getElementById("s-notebook-active").value;
    await renderNotebookPreview(id);
  });

document
  .getElementById("s-notebook-active")
  .addEventListener("change", async () => {
    const sel = document.getElementById("s-notebook-active");
    const id = sel.value;
    const name = sel.options[sel.selectedIndex]?.dataset.name || "";
    await saveConfig({ active_notebook_id: id, active_notebook_name: name });
    await renderNotebookPreview(id);
  });

async function refreshNotebooks(selectedId) {
  const sel = document.getElementById("s-notebook-active");
  const status = document.getElementById("s-notebook-status");
  const info = document.getElementById("s-notebook-info");
  try {
    const r = await fetch(`${API}/api/notebooks`);
    const data = await r.json();
    sel.innerHTML =
      '<option value="">— None (notebook integration off) —</option>';
    if (!data.ok) {
      status.textContent = "not reachable";
      status.style.color = "var(--error)";
      info.textContent = "";
      return;
    }
    status.textContent = `connected · ${data.notebooks.length} notebook${data.notebooks.length === 1 ? "" : "s"}`;
    status.style.color = "var(--accent)";
    for (const nb of data.notebooks) {
      const opt = document.createElement("option");
      opt.value = nb.id;
      opt.dataset.name = nb.name || "";
      const counts = `${nb.source_count || 0} src · ${nb.note_count || 0} notes`;
      opt.textContent = `${nb.name || "(untitled)"}  ·  ${counts}`;
      if (nb.id === selectedId) opt.selected = true;
      sel.appendChild(opt);
    }
    await renderNotebookPreview(sel.value);
  } catch (e) {
    status.textContent = "error";
    status.style.color = "var(--error)";
    info.textContent = String(e.message || e);
  }
}

// ── ComfyUI ──────────────────────────────────────────────────────────────────
function updateComfyuiCustomVisibility() {
  const v = document.getElementById("s-comfyui-workflow").value;
  document
    .getElementById("s-comfyui-custom-wrap")
    .classList.toggle("hidden", v !== "custom");
}

function fillSelect(el, options, selected) {
  el.innerHTML = "";
  if (!options || !options.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "— none found —";
    el.appendChild(opt);
    return;
  }
  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = o;
    opt.textContent = o;
    if (o === selected) opt.selected = true;
    el.appendChild(opt);
  }
  if (!options.includes(selected) && selected) {
    const opt = document.createElement("option");
    opt.value = selected;
    opt.textContent = `${selected} (not on server)`;
    opt.selected = true;
    el.insertBefore(opt, el.firstChild);
  }
}

async function refreshComfyui(c, { force = false } = {}) {
  const status = document.getElementById("s-comfyui-status");
  const ckptSel = document.getElementById("s-comfyui-checkpoint");
  const samplerSel = document.getElementById("s-comfyui-sampler");
  const schedSel = document.getElementById("s-comfyui-scheduler");
  status.textContent = "checking…";
  status.style.color = "var(--muted)";
  try {
    const r = await fetch(
      `${API}/api/comfyui/status${force ? "?refresh=1" : ""}`,
    );
    const data = await r.json();
    if (!data.ok) {
      status.textContent = data.error || "unreachable";
      status.style.color = "var(--error)";
      fillSelect(ckptSel, [], c.comfyui_checkpoint);
      fillSelect(
        samplerSel,
        ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde"],
        c.comfyui_sampler,
      );
      fillSelect(
        schedSel,
        ["normal", "karras", "exponential", "sgm_uniform"],
        c.comfyui_scheduler,
      );
      btnImage.disabled = true;
      return;
    }
    status.textContent = `connected · ${data.checkpoints.length} checkpoints`;
    status.style.color = "var(--accent)";
    fillSelect(ckptSel, data.checkpoints, c.comfyui_checkpoint);
    fillSelect(samplerSel, data.samplers, c.comfyui_sampler);
    fillSelect(schedSel, data.schedulers, c.comfyui_scheduler);
    btnImage.disabled = false;
  } catch (e) {
    status.textContent = "error: " + (e.message || e);
    status.style.color = "var(--error)";
    btnImage.disabled = true;
  }
}

// Check ComfyUI reachability once on init (so button enables/disables on first paint)
async function probeComfyuiOnce() {
  try {
    const r = await fetch(`${API}/api/comfyui/status`);
    const data = await r.json();
    btnImage.disabled = !data.ok;
    if (!data.ok)
      btnImage.title = "ComfyUI unreachable (check Settings → Image)";
    else btnImage.title = "Generate image (ComfyUI)";
  } catch (e) {
    btnImage.disabled = true;
  }
}

// Image generation modal
const imgOverlay = document.getElementById("image-overlay");
const imgPrompt = document.getElementById("img-prompt");
const imgNegative = document.getElementById("img-negative");
const imgProgress = document.getElementById("img-progress");
const imgGenerate = document.getElementById("img-generate");
let imgGenerating = false;

function openImageModal() {
  if (isStreaming || btnImage.disabled) return;
  imgPrompt.value = userInput.value.trim(); // pre-fill with current input if any
  imgNegative.value = "";
  imgProgress.textContent = "";
  imgGenerating = false;
  imgGenerate.disabled = false;
  imgOverlay.classList.remove("hidden");
  imgPrompt.focus();
}

function closeImageModal() {
  if (imgGenerating) return; // don't close mid-generation
  imgOverlay.classList.add("hidden");
}

async function generateImage() {
  const prompt = imgPrompt.value.trim();
  if (!prompt || imgGenerating) return;
  imgGenerating = true;
  imgGenerate.disabled = true;
  imgProgress.textContent = "queued…";
  // Pin the chat context at submit time so a mid-generation New Chat / chat
  // switch doesn't cause the image to land in the wrong conversation.
  const chatIdAtStart = currentChatId;
  let imageResult = null;
  try {
    const body = { prompt, chat_id: chatIdAtStart, overrides: {} };
    const neg = imgNegative.value.trim();
    if (neg) body.overrides.negative = neg;
    const resp = await fetch(`${API}/api/comfyui/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let ev;
        try {
          ev = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        if (ev.type === "queued") {
          imgProgress.textContent = "queued · waiting for worker…";
        } else if (ev.type === "progress") {
          imgProgress.textContent = `step ${ev.value}/${ev.max}`;
        } else if (ev.type === "image") {
          imageResult = ev;
        } else if (ev.type === "error") {
          imgProgress.textContent = "✗ " + ev.message;
          imgGenerating = false;
          imgGenerate.disabled = false;
          return;
        }
      }
    }
    if (imageResult) {
      imgOverlay.classList.add("hidden");
      await onImageGenerated(imageResult, chatIdAtStart);
    } else {
      imgProgress.textContent = "✗ no image returned";
    }
  } catch (e) {
    imgProgress.textContent = "✗ " + (e.message || e);
  } finally {
    imgGenerating = false;
    imgGenerate.disabled = false;
  }
}

function relativeImagePath(absPath) {
  // /home/X/.local/share/logos/images/<chat_id>/<file> → <chat_id>/<file>
  const m = absPath.match(/\/logos\/images\/(.+)$/);
  return m ? m[1] : absPath;
}

async function onImageGenerated(ev, originalChatId) {
  // Append assistant message with image. If the user navigated away from the
  // chat that initiated this generation, persist to the original chat without
  // touching the current UI.
  const rel = relativeImagePath(ev.path);
  const url = `${API}/api/images/${rel.split("/").map(encodeURIComponent).join("/")}`;
  const msg = {
    role: "assistant",
    content: "",
    image: {
      path: ev.path,
      url,
      filename: ev.filename,
      prompt: ev.prompt,
      params: ev.params || {},
    },
  };

  // Detect race: chat changed (or was assigned an id) since we started
  if (originalChatId && originalChatId !== currentChatId) {
    await persistMessageToChat(originalChatId, msg);
    return;
  }
  // If the original chat had no id yet AND user has since started a different chat,
  // discard (image file remains on disk under "scratch" folder).
  if (!originalChatId && currentChatId) return;

  conversationHistory.push(msg);
  renderAssistantImage(msg);
  scrollToBottom();
  await saveCurrentChat();

  if (currentConfig.comfyui_post_commentary) {
    await runImageCommentary(msg);
  }
}

async function persistMessageToChat(chatId, msg) {
  try {
    const r = await fetch(`${API}/api/chats/${chatId}`);
    if (!r.ok) return;
    const chat = await r.json();
    const messages = (chat.messages || []).concat([msg]);
    await fetch(`${API}/api/chats/${chatId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    await loadChatList();
  } catch (e) {
    console.error("persistMessageToChat failed:", e);
  }
}

function renderAssistantImage(msg) {
  const root = document.createElement("div");
  root.className = "msg assistant";
  const roleLabel = document.createElement("div");
  roleLabel.className = "msg-role";
  roleLabel.textContent = "assistant";
  const body = document.createElement("div");
  body.className = "msg-body";

  const wrap = document.createElement("div");
  wrap.className = "msg-image-wrap";
  const img = document.createElement("img");
  img.className = "msg-image";
  img.src = msg.image.url;
  img.alt = msg.image.prompt || "";
  img.onclick = () => openImageViewer(msg.image.url);
  const meta = document.createElement("div");
  meta.className = "msg-image-meta";
  const p = msg.image.params || {};
  meta.textContent = `“${msg.image.prompt}” · ${p.width}×${p.height} · ${p.steps} steps · seed ${p.seed} · ${p.checkpoint || "?"}`;
  wrap.append(img, meta);
  body.appendChild(wrap);

  root.append(roleLabel, body);
  if (msg.content) {
    const text = document.createElement("div");
    text.className = "msg-body";
    text.innerHTML = marked.parse(msg.content);
    root.appendChild(text);
  }
  messagesEl.appendChild(root);
  attachMessageActions(root);
  return root;
}

function openImageViewer(url) {
  let v = document.getElementById("image-viewer");
  if (!v) {
    v = document.createElement("div");
    v.id = "image-viewer";
    v.onclick = () => v.remove();
    const i = document.createElement("img");
    i.src = url;
    v.appendChild(i);
    document.body.appendChild(v);
  }
}

async function runImageCommentary(imageMsg) {
  // Synthetic continuation: append a user message asking for commentary,
  // stream response; user message removed before saving (purely meta).
  const synthetic = {
    role: "user",
    content: `[I just generated an image with the prompt: "${imageMsg.image.prompt}". Briefly describe what you imagine was produced and offer any creative observations. Keep it under 80 words.]`,
  };
  conversationHistory.push(synthetic);
  await streamResponse(false);
  // Hide the synthetic prompt from history (keep assistant reply)
  const idx = conversationHistory.indexOf(synthetic);
  if (idx !== -1) conversationHistory.splice(idx, 1);
  await saveCurrentChat();
}

async function renderNotebookPreview(notebookId) {
  const info = document.getElementById("s-notebook-info");
  const warn = document.getElementById("s-notebook-warning");
  warn.classList.add("hidden");
  if (!notebookId) {
    info.textContent = "";
    return;
  }
  info.textContent = "loading…";
  try {
    const r = await fetch(
      `${API}/api/notebooks/${encodeURIComponent(notebookId)}/preview`,
    );
    const data = await r.json();
    if (!data.ok) {
      info.textContent = data.error || "could not load";
      return;
    }
    const t = data.total_tokens_est;
    info.textContent = `${data.source_count} source${data.source_count === 1 ? "" : "s"} · ~${t.toLocaleString()} tokens`;
    if (t > 50000) {
      warn.classList.remove("hidden");
      warn.textContent = `⚠ Large notebook (~${t.toLocaleString()} tokens). May exceed your model's context window or slow responses.`;
    }
  } catch (e) {
    info.textContent = String(e.message || e);
  }
}

function updateProviderFieldsVisibility() {
  const provider = document.getElementById("s-search-provider").value;
  document
    .getElementById("s-provider-brave")
    .classList.toggle("hidden", provider !== "brave");
  document
    .getElementById("s-provider-searxng")
    .classList.toggle("hidden", provider !== "searxng");
}

// Settings tabs
document.querySelectorAll(".settings-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    document
      .querySelectorAll(".settings-tab")
      .forEach((t) => t.classList.toggle("active", t === tab));
    document
      .querySelectorAll(".settings-tab-panel")
      .forEach((p) => p.classList.toggle("active", p.dataset.panel === target));
    try {
      localStorage.setItem("logos.lastSettingsTab", target);
    } catch (_) {}
  });
});

// Restore last opened tab when Settings opens
function restoreSettingsTab() {
  let tab = "model";
  try {
    tab = localStorage.getItem("logos.lastSettingsTab") || "model";
  } catch (_) {}
  const btn = document.querySelector(`.settings-tab[data-tab="${tab}"]`);
  if (btn) btn.click();
}

// Restore last opened tab when Settings opens - event listener
const tabs = document.querySelectorAll(".settings-tab");
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    document
      .querySelectorAll(".settings-tab")
      .forEach((t) => t.classList.toggle("active", t === tab));
    document
      .querySelectorAll(".settings-tab-panel")
      .forEach((p) =>
        p.classList.toggle("active", p.dataset.panel === tab.dataset.tab),
      );
    try {
      localStorage.setItem("logos.lastSettingsTab", tab.dataset.tab);
    } catch (_) {}
  });
});

// ── D1/D2: Per-model temperature overrides ──────────────────────────────────
function getSelectedModel() {
  return document.getElementById("s-model").value;
}

function updateTempOverrideUI() {
  const model = getSelectedModel();
  const overrides = currentConfig.model_overrides || {};
  const hasOverride =
    overrides[model] && overrides[model].temperature !== undefined;
  const modeEl = document.getElementById("s-temp-mode");
  const tempSlider = document.getElementById("s-temperature");
  const tempVal = document.getElementById("s-temperature-val");

  if (hasOverride) {
    modeEl.textContent = `per-model: ${model}`;
    modeEl.style.color = "var(--accent)";
    tempSlider.value = overrides[model].temperature;
    tempVal.textContent = overrides[model].temperature;
  } else {
    modeEl.textContent = "global";
    modeEl.style.color = "";
    tempSlider.value = currentConfig.temperature || 0.7;
    tempVal.textContent = currentConfig.temperature || 0.7;
  }
  document.getElementById("s-conservative-mode").checked = hasOverride;
}

function hasTempOverride() {
  const model = getSelectedModel();
  const overrides = currentConfig.model_overrides || {};
  return overrides[model] && overrides[model].temperature !== undefined;
}

function toggleTempOverride() {
  const model = getSelectedModel();
  if (!currentConfig.model_overrides) currentConfig.model_overrides = {};
  const overrides = currentConfig.model_overrides;
  const hasOverride =
    overrides[model] && overrides[model].temperature !== undefined;

  if (hasOverride) {
    // Switch back to global: remove override for this model
    delete overrides[model].temperature;
    if (!Object.keys(overrides[model]).length) delete overrides[model];
  } else {
    // Switch to per-model: copy current global temperature as starting point
    if (!overrides[model]) overrides[model] = {};
    overrides[model].temperature = parseFloat(
      document.getElementById("s-temperature").value,
    );
  }
  updateTempOverrideUI();
}

// Event listeners for D1/D2
const sTempToggle = document.getElementById("s-temp-toggle");
if (sTempToggle) {
  sTempToggle.addEventListener("click", toggleTempOverride);
}

document.getElementById("s-model").addEventListener("change", () => {
  updateTempOverrideUI();
});

document
  .getElementById("s-conservative-mode")
  .addEventListener("change", function () {
    const model = getSelectedModel();
    if (!currentConfig.model_overrides) currentConfig.model_overrides = {};
    if (this.checked) {
      if (!currentConfig.model_overrides[model])
        currentConfig.model_overrides[model] = {};
      currentConfig.model_overrides[model].temperature = 0.4;
    } else {
      if (currentConfig.model_overrides[model]) {
        delete currentConfig.model_overrides[model].temperature;
        if (!Object.keys(currentConfig.model_overrides[model]).length) {
          delete currentConfig.model_overrides[model];
        }
      }
    }
    updateTempOverrideUI();
  });

userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage(forceSearchMode);
  }
});

userInput.addEventListener("input", autoResizeTextarea);

// Close settings on overlay click
document.getElementById("settings-overlay").addEventListener("click", (e) => {
  if (e.target.id === "settings-overlay") closeSettings();
});

// ── Notes Sidebar Event Listeners ────────────────────────────────────────────
btnNotesToggle.addEventListener("click", toggleNotesSidebar);
btnNotesClose.addEventListener("click", closeNotesSidebar);
notesDetailClose.addEventListener("click", closeNoteDetail);
notesDetailDelete.addEventListener("click", deleteCurrentNote);

// Close detail modal on overlay click
notesDetailOverlay.addEventListener("click", (e) => {
  if (e.target === notesDetailOverlay) closeNoteDetail();
});

// ESC to close detail modal
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !notesDetailOverlay.classList.contains("hidden")) {
    closeNoteDetail();
  }
});

// Search input debounce (200ms)
let notesSearchTimer = null;
notesSearch.addEventListener("input", () => {
  clearTimeout(notesSearchTimer);
  notesSearchTimer = setTimeout(() => {
    const q = notesSearch.value.trim();
    loadNotes(q || null);
  }, 200);
});

// ── Start ─────────────────────────────────────────────────────────────────────
init();
