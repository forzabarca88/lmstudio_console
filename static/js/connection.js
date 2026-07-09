/**
 * Connection management: connect, disconnect, status updates, heartbeat.
 */

import { state, saveSettings, saveCurrentSession, saveSessionHistory } from "./state.js";
import { apiCall } from "./api.js";
import { showToast } from "./ui.js";
import { renderModelList, syncLoadedModels } from "./models.js";
import { renderHistoryList } from "./history.js";

/**
 * Connect to the configured endpoint.
 * Uses OpenAI-compatible /v1/models endpoint for universal compatibility.
 * @param {Object} dom - DOM element references.
 */
export async function connect(dom) {
    if (state.connected) {
        disconnect(dom);
        return;
    }

    state.endpoint = dom.endpoint.value.trim();
    state.apiToken = dom.apiToken.value.trim();
    saveSettings();

    state.status = "connecting";
    updateStatus(dom, null, "Connecting...");
    dom.connectBtn.textContent = "Connecting...";
    dom.connectBtn.disabled = true;

    try {
        const data = await apiCall("/v1/models");
        state.connected = true;
        state.status = "connected";

        state.models = (data.data || []).map(m => ({
            key: m.id,
            type: "llm",
            display_name: m.id,
            loaded_instances: [],
        }));

        await syncLoadedModels();

        if (state.selectedModel && !state.models.some(m => m.key === state.selectedModel)) {
            state.selectedModel = null;
            saveSettings();
        }

        updateStatus(dom, true);
        dom.connectBtn.textContent = "Disconnect";
        const loadedCount = state.loadedModels.size;
        if (loadedCount > 0) {
            showToast(`Connected — ${state.models.length} models, ${loadedCount} loaded`, "info");
        } else {
            showToast(`Connected — ${state.models.length} models found`, "success");
        }

        renderModelList(dom);
        enableChatControls(dom);
        startHeartbeat(dom);
        renderHistoryList(dom);
    } catch (e) {
        state.connected = false;
        state.status = "disconnected";
        updateStatus(dom, false);
        dom.connectBtn.textContent = "Connect";
        showToast(`Connection failed: ${e.message}`, "error");
    }

    dom.connectBtn.disabled = false;
}

/**
 * Disconnect from the endpoint.
 * Preserves existing chat messages per spec.
 * @param {Object} dom - DOM element references.
 */
export function disconnect(dom) {
    stopHeartbeat();

    state.connected = false;
    state.status = "disconnected";
    state.models = [];
    state.loadedModels.clear();
    state.selectedModel = null;

    updateStatus(dom, false);
    dom.connectBtn.textContent = "Connect";
    dom.modelList.innerHTML = `<li class="empty-state" style="flex:unset;padding:20px 0;">
        <div class="empty-subtitle">Connect to an OpenAI-compatible endpoint to see available models</div>
    </li>`;

    if (state.chatMessages.length > 0) {
        saveCurrentSession();
        renderHistoryList(dom);
    }

    if (state.chatMessages.length > 0) {
        dom.emptyState.style.display = "none";
        dom.chatHeader.style.display = "flex";
        dom.chatMessages.style.display = "flex";
        dom.chatMetrics.style.display = "flex";
    } else {
        dom.emptyState.style.display = "flex";
        dom.chatHeader.style.display = "none";
        dom.chatMessages.style.display = "none";
        dom.chatMetrics.style.display = "none";
    }

    dom.chatInput.disabled = true;
    dom.sendBtn.disabled = true;
    showToast("Disconnected", "info");
}

/**
 * Update the connection status indicator.
 * @param {Object} dom
 * @param {boolean|null} connected - true=connected, false=disconnected, null=loading state
 * @param {string} statusText - override text
 */
export function updateStatus(dom, connected, statusText = null) {
    if (connected === null) {
        dom.statusDot.className = "status-dot loading";
        dom.statusText.textContent = statusText || "Loading...";
    } else if (connected) {
        dom.statusDot.className = "status-dot connected";
        dom.statusText.textContent = statusText || "Connected";
    } else {
        dom.statusDot.className = "status-dot error";
        dom.statusText.textContent = statusText || "Disconnected";
    }
}

/**
 * Enable chat controls when a model is loaded.
 * @param {Object} dom
 */
export function enableChatControls(dom) {
    const hasLoadedModel = state.models.some(m =>
        m.type === "llm" && m.loaded_instances && m.loaded_instances.length > 0
    );

    dom.chatInput.disabled = !hasLoadedModel;
    dom.sendBtn.disabled = !hasLoadedModel;

    const hasChatMessages = state.chatMessages.length > 0;

    if (hasLoadedModel && state.selectedModel) {
        dom.emptyState.style.display = "none";
        dom.chatHeader.style.display = "flex";
        dom.chatMessages.style.display = "flex";
        dom.chatMetrics.style.display = "flex";

        const model = state.models.find(m => m.key === state.selectedModel);
        if (dom.chatModelLabel) {
            dom.chatModelLabel.textContent = model?.display_name || state.selectedModel;
        }
    } else if (hasChatMessages) {
        dom.emptyState.style.display = "none";
        dom.chatHeader.style.display = "flex";
        dom.chatMessages.style.display = "flex";
        dom.chatMetrics.style.display = "flex";

        if (state.selectedModel) {
            const model = state.models.find(m => m.key === state.selectedModel);
            if (dom.chatModelLabel) {
                dom.chatModelLabel.textContent = model?.display_name || state.selectedModel;
            }
        }
    } else {
        dom.emptyState.style.display = "flex";
        dom.chatHeader.style.display = "none";
        dom.chatMessages.style.display = "none";
        dom.chatMetrics.style.display = "none";
    }
}

/**
 * Start heartbeat ping to detect disconnection.
 * @param {Object} dom - DOM element references.
 */
export function startHeartbeat(dom) {
    stopHeartbeat();
    state.heartbeatInterval = setInterval(async () => {
        if (!state.connected) return;
        try {
            await apiCall("/v1/models");
        } catch {
            state.connected = false;
            state.status = "disconnected";
            updateStatus(dom, false);
            const connectBtn = dom.connectBtn;
            connectBtn.textContent = "Connect";
            showToast("Connection lost", "error");
            stopHeartbeat();
        }
    }, 30000);
}

/**
 * Stop heartbeat ping.
 */
export function stopHeartbeat() {
    if (state.heartbeatInterval) {
        clearInterval(state.heartbeatInterval);
        state.heartbeatInterval = null;
    }
}
