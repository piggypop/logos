// ── State ───────────────────────────────────────────────────────────────────
const API = "";
let conversationHistory = [];  // [{role, content, attachments?}, ...]
let isStreaming = false;
let currentConfig = {};
let currentChatId = null;
let pendingAttachments = [];   // staged for next send

// ── DOM Refs ─────────────────────────────────────────────────────────────────
const messagesEl    = document.getElementById("messages");
const userInput     = document.getElementById("user-input");
const btnSend       = document.getElementById("btn-send");
const btnSearch     = document.getElementById("btn-search");
const btnAttach     = document.getElementById("btn-attach");
const attachmentsBar = document.getElementById("attachments-bar");
const btnNewChat    = document.getElementById("btn-new-chat");
const btnSettings   = document.getElementById("btn-settings");
const btnSidebarToggle = document.getElementById("btn-sidebar-toggle");
const btnSidebarClose  = document.getElementById("btn-sidebar-close");
const sidebar       = document.getElementById("sidebar");
const chatListEl    = document.getElementById("chat-list");
const modelBadge    = document.getElementById("model-badge");
const searchIndicator = document.getElementById("search-indicator");
const searchLabel   = document.getElementById("search-label");

// ── Init ─────────────────────────────────────────────────────────────────────
async function init() {
  await loadConfig();
  modelBadge.textContent = currentConfig.ollama_model || "—";

  // Configure marked
  marked.setOptions({
    breaks: true,
    gfm: true,
    highlight: function(code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    }
  });

  await loadChatList();
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
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data)
  });
  currentConfig = {...currentConfig, ...data};
  modelBadge.textContent = currentConfig.ollama_model || "—";
}

// ── Send Message ─────────────────────────────────────────────────────────────
async function sendMessage(forceSearch = false) {
  const text = userInput.value.trim();
  if ((!text && !pendingAttachments.length) || isStreaming) return;

  const validAttachments = pendingAttachments.filter(a => !a.error);
  const userMsg = {role: "user", content: text};
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
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        messages: conversationHistory,
        force_search: forceSearch
      })
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;

      // {stream: true} keeps partial multi-byte UTF-8 chars between reads
      sseLineBuffer += decoder.decode(value, {stream: true});
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
          assistantBubble.querySelectorAll("pre code").forEach(el => hljs.highlightElement(el));
          conversationHistory.push({role: "assistant", content: buffer, sources});
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
      setTimeout(() => { copyBtn.textContent = "⎘ Copy"; copyBtn.classList.remove("flash"); }, 1200);
    } catch (e) {
      console.error("Copy failed:", e);
    }
  };

  const regenBtn = document.createElement("button");
  regenBtn.className = "msg-action-btn";
  regenBtn.textContent = "↻ Regenerate";
  regenBtn.onclick = () => regenerateFromMessage(msgEl);

  bar.append(copyBtn, regenBtn);
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

async function regenerateFromMessage(msgEl) {
  if (isStreaming) return;
  const all = [...messagesEl.querySelectorAll(".msg.assistant")];
  const targetIdx = all.indexOf(msgEl);
  if (targetIdx === -1) return;

  // find corresponding index in conversationHistory
  let count = -1, historyIdx = -1;
  for (let i = 0; i < conversationHistory.length; i++) {
    if (conversationHistory[i].role === "assistant") {
      count++;
      if (count === targetIdx) { historyIdx = i; break; }
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
  return {image: "🖼", audio: "🎵", video: "🎞"}[category] || "📄";
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
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.pick_files) {
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
    name.title = a.error ? a.error : `${a.category} · ${formatBytes(a.size || 0)}`;

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
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({messages: conversationHistory})
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
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  const weekAgo = new Date(today); weekAgo.setDate(today.getDate() - 7);

  const groups = {Today: [], Yesterday: [], "Last 7 days": [], Older: []};
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
  renameBtn.onclick = (e) => { e.stopPropagation(); renameChat(chat.id, chat.title); };

  const exportBtn = document.createElement("button");
  exportBtn.textContent = "↓";
  exportBtn.title = "Export JSON";
  exportBtn.onclick = (e) => { e.stopPropagation(); exportChat(chat.id); };

  const delBtn = document.createElement("button");
  delBtn.textContent = "✕";
  delBtn.title = "Delete";
  delBtn.onclick = (e) => { e.stopPropagation(); deleteChat(chat.id, chat.title); };

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
        const bubble = createAssistantBubble();
        bubble.innerHTML = marked.parse(m.content || "");
        bubble.querySelectorAll("pre code").forEach(el => hljs.highlightElement(el));
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
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({title: trimmed})
    });
    await loadChatList();
  } catch (e) {
    console.error("Rename failed:", e);
  }
}

async function exportChat(chatId) {
  // Native save dialog via pywebview js_api (works inside the desktop app)
  if (window.pywebview && window.pywebview.api && window.pywebview.api.export_chat) {
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
    const safeTitle = (data.title || "chat").replace(/[^\w\-]+/g, "_").slice(0, 60);
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
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
    await fetch(`${API}/api/chats/${chatId}`, {method: "DELETE"});
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

// ── Settings ─────────────────────────────────────────────────────────────────
async function openSettings() {
  await loadConfig();
  const c = currentConfig;

  document.getElementById("s-ollama-host").value = c.ollama_host || "";
  document.getElementById("s-searxng-url").value = c.searxng_url || "";
  document.getElementById("s-results-count").value = c.searxng_results_count || 5;
  document.getElementById("s-auto-search").checked = !!c.auto_search_enabled;
  document.getElementById("s-user-location").value = c.user_location || "";
  document.getElementById("s-system-prompt").value = c.system_prompt || "";
  document.getElementById("s-temperature").value = c.temperature || 0.7;
  document.getElementById("s-temperature-val").textContent = c.temperature || 0.7;

  // Load models
  await refreshModels(c.ollama_model);

  // Load memory
  await loadMemory();

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
    await fetch(`${API}/api/memory/${fact.id}`, {method: "DELETE"});
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
    searxng_url: document.getElementById("s-searxng-url").value.trim(),
    searxng_results_count: parseInt(document.getElementById("s-results-count").value),
    auto_search_enabled: document.getElementById("s-auto-search").checked,
    user_location: document.getElementById("s-user-location").value.trim(),
    system_prompt: document.getElementById("s-system-prompt").value,
    temperature: temp
  });
  closeSettings();
}

// ── Event Listeners ───────────────────────────────────────────────────────────
btnSend.addEventListener("click", () => sendMessage(false));
btnSearch.addEventListener("click", () => sendMessage(true));
btnAttach.addEventListener("click", pickAttachments);
btnNewChat.addEventListener("click", newChat);
btnSettings.addEventListener("click", openSettings);
btnSidebarToggle.addEventListener("click", toggleSidebar);
btnSidebarClose.addEventListener("click", toggleSidebar);

document.getElementById("settings-close").addEventListener("click", closeSettings);
document.getElementById("settings-cancel").addEventListener("click", closeSettings);
document.getElementById("settings-save").addEventListener("click", saveSettings);

document.getElementById("s-refresh-models").addEventListener("click", () => {
  refreshModels(document.getElementById("s-model").value);
});

document.getElementById("s-memory-refresh").addEventListener("click", loadMemory);

document.getElementById("s-temperature").addEventListener("input", function() {
  document.getElementById("s-temperature-val").textContent = this.value;
});

userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage(false);
  }
});

userInput.addEventListener("input", autoResizeTextarea);

// Close settings on overlay click
document.getElementById("settings-overlay").addEventListener("click", (e) => {
  if (e.target.id === "settings-overlay") closeSettings();
});

// ── Start ─────────────────────────────────────────────────────────────────────
init();
