/**
 * Main entry point - wires all modules together and binds events.
 */

import { state, saveSettings, loadSettings, saveSessionHistory } from "./state.js";
import { connect, disconnect } from "./connection.js";
import { refreshModels, loadModel, unloadModel } from "./models.js";
import { sendMessage, newChat, clearAttachments, renderAttachmentPreview } from "./chat.js";
import { renderHistoryList, continueSession, deleteSession } from "./history.js";
import { showToast, autoResizeInput, updateMetrics } from "./ui.js";

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
};

/* ═══════════════════════════════════════════
   MARKED CONFIG
   ═══════════════════════════════════════════ */
marked.setOptions({
    breaks: true,
    gfm: true,
});

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

// Settings panel toggle
dom.settingsToggle.addEventListener("click", () => {
    dom.settingsToggle.classList.toggle("open");
    dom.settingsPanel.classList.toggle("open");
});

// History panel toggle
dom.historyToggle.addEventListener("click", () => {
    dom.historyToggle.classList.toggle("open");
    dom.historyPanel.classList.toggle("open");
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

dom.sendBtn.addEventListener("click", () => sendMessage(dom));
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

// Mobile sidebar toggle
dom.sidebarToggle.addEventListener("click", () => {
    dom.sidebar.classList.toggle("collapsed");
});

/* ═══════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════ */
loadSettings(dom);
autoResizeInput(dom.chatInput);
renderHistoryList(dom);
