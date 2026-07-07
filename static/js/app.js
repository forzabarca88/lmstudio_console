/* ═══════════════════════════════════════════
   STATE
   ═══════════════════════════════════════════ */
const state = {
    endpoint: "http://localhost:1234",
    apiToken: "",
    connected: false,
    models: [],
    loadedModels: new Set(),
    selectedModel: null,
    chatMessages: [],
    systemPrompt: "You are a helpful assistant.",
    temperature: 0.7,
    streaming: false,
};

// Persisted settings key
const SETTINGS_KEY = "lm_console_settings";

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
    chatModelLabel: document.getElementById("chatModelLabel"),
    newChatBtn: document.getElementById("newChatBtn"),
    chatInput: document.getElementById("chatInput"),
    sendBtn: document.getElementById("sendBtn"),
    toastContainer: document.getElementById("toastContainer"),
    sidebar: document.getElementById("sidebar"),
    sidebarToggle: document.getElementById("sidebarToggle"),
};

/* ═══════════════════════════════════════════
   PERSISTENCE
   ═══════════════════════════════════════════ */
function saveSettings() {
    const settings = {
        endpoint: state.endpoint,
        apiToken: state.apiToken,
        systemPrompt: state.systemPrompt,
        temperature: state.temperature,
    };
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

function loadSettings() {
    try {
        const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
        if (saved) {
            if (saved.endpoint) {
                state.endpoint = saved.endpoint;
                dom.endpoint.value = saved.endpoint;
            }
            if (saved.apiToken) {
                state.apiToken = saved.apiToken;
                dom.apiToken.value = saved.apiToken;
            }
            if (saved.systemPrompt) {
                state.systemPrompt = saved.systemPrompt;
                dom.systemPrompt.value = saved.systemPrompt;
            }
            if (saved.temperature !== undefined) {
                state.temperature = saved.temperature;
                dom.temperature.value = saved.temperature;
                dom.temperatureValue.textContent = saved.temperature.toFixed(2);
            }
        }
    } catch (e) {
        // Ignore parse errors
    }
}

/* ═══════════════════════════════════════════
   TOAST NOTIFICATIONS
   ═══════════════════════════════════════════ */
function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    dom.toastContainer.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(100%)";
        toast.style.transition = "all 0.2s";
        setTimeout(() => toast.remove(), 200);
    }, 3500);
}

/* ═══════════════════════════════════════════
   API CALLS
   ═══════════════════════════════════════════ */
async function apiCall(path, method = "GET", body = null) {
    const headers = { "Content-Type": "application/json" };
    if (state.apiToken) {
        headers["Authorization"] = `Bearer ${state.apiToken}`;
    }

    const options = { method, headers };
    if (body) options.body = JSON.stringify(body);

    // Use proxy to avoid CORS
    const url = `/proxy${path}`;

    const response = await fetch(url, options);
    if (!response.ok) {
        const errorBody = await response.text();
        throw new Error(`API error ${response.status}: ${errorBody}`);
    }
    return response.json();
}

/* ═══════════════════════════════════════════
   CONNECTION
   ═══════════════════════════════════════════ */
async function connect() {
    // If already connected, disconnect
    if (state.connected) {
        disconnect();
        return;
    }

    state.endpoint = dom.endpoint.value.trim();
    state.apiToken = dom.apiToken.value.trim();
    saveSettings();

    dom.connectBtn.textContent = "Connecting...";
    dom.connectBtn.disabled = true;

    try {
        // Test connection by listing models
        const data = await apiCall("/api/v1/models");
        state.connected = true;
        state.models = data.models || [];

        updateStatus(true);
        dom.connectBtn.textContent = "Disconnect";
        showToast("Connected to LM Studio", "success");

        renderModelList();
        enableChatControls();
    } catch (e) {
        state.connected = false;
        updateStatus(false);
        dom.connectBtn.textContent = "Connect";
        showToast(`Connection failed: ${e.message}`, "error");
    }

    dom.connectBtn.disabled = false;
}

function disconnect() {
    state.connected = false;
    state.models = [];
    state.loadedModels.clear();
    state.selectedModel = null;
    state.chatMessages = [];
    dom.chatMessages.innerHTML = "";

    updateStatus(false);
    dom.connectBtn.textContent = "Connect";
    dom.modelList.innerHTML = `<li class="empty-state" style="flex:unset;padding:20px 0;">
        <div class="empty-subtitle">Connect to LM Studio to see available models</div>
    </li>`;
    disableChatControls();
    showToast("Disconnected", "info");
}

function updateStatus(connected) {
    dom.statusDot.className = `status-dot ${connected ? "connected" : "error"}`;
    dom.statusText.textContent = connected ? "Connected" : "Disconnected";
}

/* ═══════════════════════════════════════════
   MODEL MANAGEMENT
   ═══════════════════════════════════════════ */
async function refreshModels() {
    if (!state.connected) {
        showToast("Not connected", "error");
        return;
    }

    dom.refreshModelsBtn.textContent = "↻ Loading...";
    dom.refreshModelsBtn.disabled = true;

    try {
        const data = await apiCall("/api/v1/models");
        state.models = data.models || [];

        // Track which models are loaded
        state.loadedModels.clear();
        for (const model of state.models) {
            if (model.loaded_instances && model.loaded_instances.length > 0) {
                for (const inst of model.loaded_instances) {
                    state.loadedModels.add(inst.id);
                }
            }
        }

        renderModelList();
        showToast(`Found ${state.models.length} models`, "info");
    } catch (e) {
        showToast(`Refresh failed: ${e.message}`, "error");
    }

    dom.refreshModelsBtn.textContent = "↻ Refresh";
    dom.refreshModelsBtn.disabled = false;
}

function renderModelList() {
    const llmModels = state.models.filter(m => m.type === "llm");

    if (llmModels.length === 0) {
        dom.modelList.innerHTML = `<li class="empty-state" style="flex:unset;padding:20px 0;">
            <div class="empty-subtitle">No LLM models available</div>
        </li>`;
        return;
    }

    dom.modelList.innerHTML = llmModels.map(model => {
        const isLoaded = state.loadedModels.has(model.key) ||
                         (model.loaded_instances && model.loaded_instances.length > 0);
        const isSelected = state.selectedModel === model.key;

        const sizeStr = model.size_bytes ? formatBytes(model.size_bytes) : "";
        const paramsStr = model.params_string || "";
        const quantStr = model.quantization?.name || "";
        const meta = [paramsStr, quantStr, sizeStr].filter(Boolean).join(" · ");

        return `<li class="model-item ${isLoaded ? 'loaded' : ''} ${isSelected ? 'selected' : ''}"
                     data-key="${model.key}"
                     data-instance-id="${model.loaded_instances?.[0]?.id || ''}">
            <div class="model-info">
                <div class="model-name">${escapeHtml(model.display_name || model.key)}</div>
                <div class="model-meta">${meta}</div>
            </div>
            ${isLoaded ? '<span class="model-badge badge-loaded">loaded</span>' : ''}
        </li>`;
    }).join("");

    // Click handlers for model items
    dom.modelList.querySelectorAll(".model-item").forEach(item => {
        item.addEventListener("click", () => {
            const key = item.dataset.key;
            if (state.selectedModel !== key) {
                // Switching models - clear chat history
                state.chatMessages = [];
                dom.chatMessages.innerHTML = "";
            }
            state.selectedModel = key;
            renderModelList();
            enableChatControls();
        });
    });
}

async function loadModel() {
    if (!state.selectedModel) {
        showToast("Select a model first", "error");
        return;
    }

    dom.loadModelBtn.textContent = "Loading...";
    dom.loadModelBtn.disabled = true;

    try {
        const data = await apiCall("/api/v1/models/load", "POST", {
            model: state.selectedModel,
            echo_load_config: true,
        });

        state.loadedModels.add(data.instance_id);
        renderModelList();
        enableChatControls();
        showToast(`Model loaded in ${data.load_time_seconds?.toFixed(1) || '?'}s`, "success");
    } catch (e) {
        showToast(`Load failed: ${e.message}`, "error");
    }

    dom.loadModelBtn.textContent = "Load";
    dom.loadModelBtn.disabled = false;
}

async function unloadModel() {
    if (!state.selectedModel) {
        showToast("Select a model first", "error");
        return;
    }

    // Find the instance ID for this model
    const model = state.models.find(m => m.key === state.selectedModel);
    const instanceId = model?.loaded_instances?.[0]?.id || state.selectedModel;

    dom.unloadModelBtn.textContent = "Unloading...";
    dom.unloadModelBtn.disabled = true;

    try {
        await apiCall("/api/v1/models/unload", "POST", { instance_id: instanceId });

        state.loadedModels.delete(instanceId);
        state.selectedModel = null;
        state.chatMessages = [];
        dom.chatMessages.innerHTML = "";
        renderModelList();
        disableChatControls();
        showToast("Model unloaded", "success");
    } catch (e) {
        showToast(`Unload failed: ${e.message}`, "error");
    }

    dom.unloadModelBtn.textContent = "Unload";
    dom.unloadModelBtn.disabled = false;
}

/* ═══════════════════════════════════════════
   CHAT
   ═══════════════════════════════════════════ */
function enableChatControls() {
    const hasLoadedModel = state.models.some(m =>
        m.type === "llm" &&
        (state.loadedModels.has(m.key) || (m.loaded_instances && m.loaded_instances.length > 0))
    );

    dom.chatInput.disabled = !hasLoadedModel;
    dom.sendBtn.disabled = !hasLoadedModel;

    if (hasLoadedModel && state.selectedModel) {
        dom.emptyState.style.display = "none";
        dom.chatHeader.style.display = "flex";
        dom.chatMessages.style.display = "flex";

        const model = state.models.find(m => m.key === state.selectedModel);
        dom.chatModelLabel.textContent = model?.display_name || state.selectedModel;
    } else if (!hasLoadedModel) {
        dom.emptyState.style.display = "flex";
        dom.chatHeader.style.display = "none";
        dom.chatMessages.style.display = "none";
    }
}

function disableChatControls() {
    dom.chatInput.disabled = true;
    dom.sendBtn.disabled = true;
    dom.emptyState.style.display = "flex";
    dom.chatHeader.style.display = "none";
    dom.chatMessages.style.display = "none";
    state.selectedModel = null;
    state.chatMessages = [];
}

function newChat() {
    state.chatMessages = [];
    dom.chatMessages.innerHTML = "";
    showToast("Chat cleared", "info");
}

async function sendMessage() {
    const text = dom.chatInput.value.trim();
    if (!text || state.streaming || !state.selectedModel) return;

    state.streaming = true;
    dom.chatInput.disabled = true;
    dom.sendBtn.disabled = true;
    dom.chatInput.value = "";
    autoResizeInput();

    // Add user message
    const userMsg = { role: "user", content: text };
    state.chatMessages.push(userMsg);
    appendMessage(userMsg, "user");

    // Create assistant message placeholder
    const assistantEl = appendMessage({ role: "assistant", content: "" }, "assistant");
    const contentEl = assistantEl.querySelector(".message-text");

    // Build messages array with system prompt
    const messages = [];
    if (state.systemPrompt.trim()) {
        messages.push({ role: "system", content: state.systemPrompt });
    }
    messages.push(...state.chatMessages);

    try {
        // Use OpenAI-compatible streaming endpoint
        const response = await fetch("/proxy/v1/chat/completions", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...(state.apiToken ? { "Authorization": `Bearer ${state.apiToken}` } : {}),
            },
            body: JSON.stringify({
                model: state.selectedModel,
                messages: messages,
                temperature: state.temperature,
                stream: true,
            }),
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Chat error ${response.status}: ${errorText}`);
        }

        // Parse SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let assistantContent = "";
        let streamDone = false;

        while (!streamDone) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Process complete lines
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    const payload = line.slice(6).trim();
                    if (payload === "[DONE]") {
                        streamDone = true;
                        break;
                    }
                    try {
                        const parsed = JSON.parse(payload);
                        const delta = parsed.choices?.[0]?.delta?.content;
                        if (delta) {
                            assistantContent += delta;
                            contentEl.innerHTML = marked.parse(assistantContent);
                            scrollToBottom();
                        }
                    } catch (e) {
                        // Skip unparseable lines
                    }
                }
            }
        }

        // Save final assistant message
        const assistantMsg = { role: "assistant", content: assistantContent };
        state.chatMessages.push(assistantMsg);
        contentEl.innerHTML = marked.parse(assistantContent);

    } catch (e) {
        contentEl.textContent = `Error: ${e.message}`;
        contentEl.style.color = "var(--danger)";
        showToast(`Chat failed: ${e.message}`, "error");
    }

    state.streaming = false;
    dom.chatInput.disabled = false;
    dom.sendBtn.disabled = false;
    dom.chatInput.focus();
}

function appendMessage(msg, role) {
    const el = document.createElement("div");
    el.className = `message ${role}`;

    const avatar = role === "user" ? "U" : "AI";
    const roleLabel = role === "user" ? "You" : "Assistant";

    el.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <div class="message-role">${roleLabel}</div>
            <div class="message-text">${msg.content ? marked.parse(msg.content) : ''}</div>
        </div>
    `;

    dom.chatMessages.appendChild(el);
    scrollToBottom();
    return el;
}

function scrollToBottom() {
    dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
}

/* ═══════════════════════════════════════════
   UTILITIES
   ═══════════════════════════════════════════ */
function formatBytes(bytes) {
    if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + " GB";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
    return bytes + " B";
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function autoResizeInput() {
    dom.chatInput.style.height = "auto";
    dom.chatInput.style.height = Math.min(dom.chatInput.scrollHeight, 160) + "px";
}

/* ═══════════════════════════════════════════
   EVENT BINDINGS
   ═══════════════════════════════════════════ */

// Connection
dom.connectBtn.addEventListener("click", connect);
dom.endpoint.addEventListener("change", () => {
    state.endpoint = dom.endpoint.value.trim();
    saveSettings();
});
dom.apiToken.addEventListener("change", () => {
    state.apiToken = dom.apiToken.value.trim();
    saveSettings();
});

// Model management
dom.refreshModelsBtn.addEventListener("click", refreshModels);
dom.loadModelBtn.addEventListener("click", loadModel);
dom.unloadModelBtn.addEventListener("click", unloadModel);

// Settings
dom.settingsToggle.addEventListener("click", () => {
    dom.settingsToggle.classList.toggle("open");
    dom.settingsPanel.classList.toggle("open");
});

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
        sendMessage();
    }
});

dom.chatInput.addEventListener("input", autoResizeInput);

dom.sendBtn.addEventListener("click", sendMessage);
dom.newChatBtn.addEventListener("click", newChat);

// Mobile sidebar toggle
dom.sidebarToggle.addEventListener("click", () => {
    dom.sidebar.classList.toggle("collapsed");
});

/* ═══════════════════════════════════════════
   MARKED CONFIG
   ═══════════════════════════════════════════ */
marked.setOptions({
    breaks: true,
    gfm: true,
});

/* ═══════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════ */
loadSettings();
autoResizeInput();
