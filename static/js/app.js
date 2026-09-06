/**
 * Main entry point - wires all modules together and binds events.
 */

import { state, saveSettings, loadSettings, saveSessionHistory, applyTheme, MOBILE_QUERY } from "./state.js";
import { connect, disconnect } from "./connection.js";
import { refreshModels, loadModel, unloadModel } from "./models.js";
import { sendMessage, newChat, clearAttachments, renderAttachmentPreview, cancelRequest } from "./chat.js";
import { renderHistoryList, continueSession, deleteSession } from "./history.js";
import { showToast, autoResizeInput, updateMetrics } from "./ui.js";
import { connectTraceLog, disconnectTraceLog, clearTraceLog, togglePause } from "./trace.js";

/* ═══════════════════════════════════════════
   BREAKPOINT
   ═══════════════════════════════════════════ */
// Mobile breakpoint for matchMedia — single source of truth is the
// MOBILE_QUERY export in state.js (see the sync note there).

/* ═══════════════════════════════════════════
   DOM REFERENCES
   ═══════════════════════════════════════════ */
const dom = {
    endpoint: document.getElementById("endpoint"),
    apiToken: document.getElementById("apiToken"),
    connectBtn: document.getElementById("connectBtn"),
    refreshModelsBtn: document.getElementById("refreshModelsBtn"),
    loadModelBtn: document.getElementById("loadModelBtn"),
    unloadModelBtn: document.getElementById("unloadModelBtn"),
    modelList: document.getElementById("modelList"),
    settingsToggle: document.getElementById("settingsToggle"),
    settingsPanel: document.getElementById("settingsPanel"),
    systemPrompt: document.getElementById("systemPrompt"),
    temperature: document.getElementById("temperature"),
    temperatureValue: document.getElementById("temperatureValue"),
    toolCallToggle: document.getElementById("toolCallToggle"),
    themeSelect: document.getElementById("themeSelect"),
    historyToggle: document.getElementById("historyToggle"),
    historyPanel: document.getElementById("historyPanel"),
    historyList: document.getElementById("historyList"),
    statusDot: document.getElementById("statusDot"),
    statusText: document.getElementById("statusText"),
    emptyState: document.getElementById("emptyState"),
    chatHeader: document.getElementById("chatHeader"),
    chatMetrics: document.getElementById("chatMetrics"),
    metricTpsValue: document.getElementById("metricTpsValue"),
    metricTtftValue: document.getElementById("metricTtftValue"),
    metricTokensValue: document.getElementById("metricTokensValue"),
    chatMessages: document.getElementById("chatMessages"),
    streamingIndicator: document.getElementById("streamingIndicator"),
    streamingIndicatorText: document.getElementById("streamingIndicatorText"),
    chatModelLabel: document.getElementById("chatModelLabel"),
    newChatBtn: document.getElementById("newChatBtn"),
    chatInput: document.getElementById("chatInput"),
    sendBtn: document.getElementById("sendBtn"),
    attachBtn: document.getElementById("attachBtn"),
    fileInput: document.getElementById("fileInput"),
    attachmentPreview: document.getElementById("attachmentPreview"),
    attachmentList: document.getElementById("attachmentList"),
    toastContainer: document.getElementById("toastContainer"),
    sidebar: document.getElementById("sidebar"),
    sidebarToggle: document.getElementById("sidebarToggle"),
    traceToggle: document.getElementById("traceToggle"),
    tracePanel: document.getElementById("tracePanel"),
    traceLog: document.getElementById("traceLog"),
    traceClearBtn: document.getElementById("traceClearBtn"),
    tracePauseBtn: document.getElementById("tracePauseBtn"),
};

/* ═══════════════════════════════════════════
   MARKED CONFIG
   ═══════════════════════════════════════════ */
// Guard: marked is loaded as a classic script from /static/vendor/ before
// this module graph loads, but if the vendored file is missing (e.g. an
// incomplete deploy), a top-level ReferenceError here would kill the entire
// ES-module graph at import time. Degrade gracefully instead.
if (typeof marked !== "undefined") {
    marked.setOptions({
        breaks: true,
        gfm: true,
    });
}

/* ═══════════════════════════════════════════
   EVENT BINDINGS
   ═══════════════════════════════════════════ */

// Connection
dom.connectBtn.addEventListener("click", () => connect(dom));
dom.endpoint.addEventListener("change", () => {
    state.endpoint = dom.endpoint.value.trim();
    saveSettings();
});
dom.apiToken.addEventListener("change", () => {
    state.apiToken = dom.apiToken.value.trim();
    saveSettings();
});

// Model management
dom.refreshModelsBtn.addEventListener("click", () => refreshModels(dom));
dom.loadModelBtn.addEventListener("click", () => loadModel(dom));
dom.unloadModelBtn.addEventListener("click", () => unloadModel(dom));

// When a collapsible panel opens on mobile, scroll its first field into
// view within the sidebar's scroll context. On narrow viewports the
// expanded content lands below the sidebar panel's fold; without this the
// user sees an empty expansion and cannot find the controls (e.g. system
// prompt / theme). scrollIntoView() may move any scrollable ancestor, but
// in this layout .sidebar-scroll is the only scroll container in the chain
// (the body clips viewport scrolling), and {block: "nearest"} is a no-op
// when the field is already fully visible. The scroll runs synchronously
// and relies on the panel's max-height transition being skipped (`0` to
// `none` is non-interpolatable), so the layout is already final here.
// Desktop is intentionally skipped: the sidebar spans the full window
// height and the user may have deliberately scrolled it — do not yank.
function revealPanelContent(panel, firstField) {
    if (!panel.classList.contains("open") || !firstField) return;
    if (typeof window.matchMedia !== "function" || !window.matchMedia(MOBILE_QUERY).matches) return;
    firstField.scrollIntoView({ block: "nearest" });
}

// Settings panel toggle
dom.settingsToggle.addEventListener("click", () => {
    dom.settingsToggle.classList.toggle("open");
    dom.settingsPanel.classList.toggle("open");
    revealPanelContent(dom.settingsPanel, dom.systemPrompt);
});

// History panel toggle
dom.historyToggle.addEventListener("click", () => {
    dom.historyToggle.classList.toggle("open");
    dom.historyPanel.classList.toggle("open");
    revealPanelContent(dom.historyPanel, dom.historyList);
});

// Settings persistence
dom.systemPrompt.addEventListener("change", () => {
    state.systemPrompt = dom.systemPrompt.value;
    saveSettings();
});

dom.temperature.addEventListener("input", () => {
    state.temperature = parseFloat(dom.temperature.value);
    dom.temperatureValue.textContent = state.temperature.toFixed(2);
    saveSettings();
});

// Tool call toggle
dom.toolCallToggle.addEventListener("change", () => {
    state.toolCallEnabled = dom.toolCallToggle.checked;
    saveSettings();
    showToast(state.toolCallEnabled ? "Tool calls enabled" : "Tool calls disabled", "info");
});

// Theme selector
dom.themeSelect.addEventListener("change", () => {
    applyTheme(dom.themeSelect.value);
});

// File attachment
dom.attachBtn.addEventListener("click", () => {
    dom.fileInput.click();
});

dom.fileInput.addEventListener("change", (e) => {
    const files = Array.from(e.target.files);
    if (files.length === 0) return;
    files.forEach(f => addAttachment(f, dom));
    e.target.value = "";
});

// Chat input
dom.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage(dom);
    }
});

dom.chatInput.addEventListener("input", () => autoResizeInput(dom.chatInput));

dom.sendBtn.addEventListener("click", () => { if (state.streaming) { cancelRequest(dom) } else { sendMessage(dom) } });
dom.newChatBtn.addEventListener("click", () => newChat(dom));

// Attachment helpers
function addAttachment(file, dom) {
    const maxSize = 50 * 1024 * 1024; // 50MB
    if (file.size > maxSize) {
        showToast(`File too large: ${file.name} (${(file.size / 1024 / 1024).toFixed(1)}MB)`, "error");
        return;
    }

    const attachment = {
        name: file.name,
        type: file.type,
        size: file.size,
        file: file,
    };

    // For images, create preview
    if (file.type.startsWith("image/")) {
        const reader = new FileReader();
        reader.onload = () => {
            attachment.preview = reader.result;
            renderAttachmentPreview(dom);
        };
        reader.readAsDataURL(file);
    }

    state.attachments.push(attachment);
    if (!attachment.preview) {
        renderAttachmentPreview(dom);
    }
}

window.removeAttachment = function(index) {
    state.attachments.splice(index, 1);
    renderAttachmentPreview(dom);
};

// Keep the toggle button's label, chevron character, and aria-expanded in
// sync with the sidebar's collapsed state and the current breakpoint.
// Mobile collapses upward (▲ expanded / ▼ collapsed); desktop collapses to
// a left rail (◀ expanded / ▶ collapsed).
function syncSidebarToggle() {
    const collapsed = dom.sidebar.classList.contains("collapsed");
    const isMobile = window.matchMedia(MOBILE_QUERY).matches;
    dom.sidebarToggle.querySelector("span:first-child").textContent =
        collapsed ? "Show sidebar" : "Hide sidebar";
    dom.sidebarToggle.querySelector(".sidebar-chevron").textContent =
        isMobile ? (collapsed ? "▼" : "▲") : (collapsed ? "▶" : "◀");
    dom.sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
}

// Sidebar toggle (desktop: collapse/expand width; mobile: collapse/expand height)
dom.sidebarToggle.addEventListener("click", () => {
    dom.sidebar.classList.toggle("collapsed");
    // Record the explicit user choice (null = never toggled) and persist it
    // so the collapse state survives reloads.
    state.sidebarCollapsed = dom.sidebar.classList.contains("collapsed");
    saveSettings();
    syncSidebarToggle();
});

// Trace log panel
dom.traceToggle.addEventListener("click", () => {
    dom.traceToggle.classList.toggle("open");
    dom.tracePanel.classList.toggle("open");
    if (dom.tracePanel.classList.contains("open")) {
        connectTraceLog(dom);
    } else {
        disconnectTraceLog();
    }
});

dom.traceClearBtn.addEventListener("click", () => clearTraceLog(dom));
dom.tracePauseBtn.addEventListener("click", () => togglePause(dom));

// Surface localStorage persistence problems (quota exceeded): state.js
// trims session history to fit and warns us; show a toast and refresh the
// history list so the UI matches what was actually persisted.
// Rate-limited: saveSettings() fires on every slider tick, so without a
// cooldown one persistent problem would toast on every event.
let lastStorageWarningAt = 0;
window.addEventListener("lmconsole:storage-warning", (e) => {
    if (e.detail?.historyChanged) renderHistoryList(dom);
    const now = Date.now();
    if (now - lastStorageWarningAt > 15000) {
        lastStorageWarningAt = now;
        showToast(e.detail?.message || "Storage error", "error");
    }
});

/* ═══════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════ */
loadSettings(dom);
// The sidebar starts expanded on every viewport (mobile included) so the
// connection, model, and settings panels are always reachable — collapsing
// is opt-in via the toggle. An explicit persisted choice applied by
// loadSettings() above still wins. No resize listener: crossing the
// breakpoint mid-session keeps the current state.
syncSidebarToggle();
applyTheme(state.theme);
if (dom.themeSelect) dom.themeSelect.value = state.theme;
autoResizeInput(dom.chatInput);
renderHistoryList(dom);
