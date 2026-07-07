/**
 * Main entry point - wires all modules together and binds events.
 */

import { state, saveSettings, loadSettings } from "./state.js";
import { connect, disconnect } from "./connection.js";
import { refreshModels, loadModel, unloadModel } from "./models.js";
import { sendMessage, newChat } from "./chat.js";
import { showToast, autoResizeInput } from "./ui.js";

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
    statusDot: document.getElementById("statusDot"),
    statusText: document.getElementById("statusText"),
    emptyState: document.getElementById("emptyState"),
    chatHeader: document.getElementById("chatHeader"),
    chatMessages: document.getElementById("chatMessages"),
    streamingIndicator: document.getElementById("streamingIndicator"),
    chatModelLabel: document.getElementById("chatModelLabel"),
    newChatBtn: document.getElementById("newChatBtn"),
    chatInput: document.getElementById("chatInput"),
    sendBtn: document.getElementById("sendBtn"),
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

// Mobile sidebar toggle
dom.sidebarToggle.addEventListener("click", () => {
    dom.sidebar.classList.toggle("collapsed");
});

/* ═══════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════ */
loadSettings(dom);
autoResizeInput(dom.chatInput);
