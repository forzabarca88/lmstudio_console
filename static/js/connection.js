/**
 * Connection management: connect, disconnect, status updates.
 */

import { state, saveSettings } from "./state.js";
import { apiCall } from "./api.js";
import { showToast } from "./ui.js";
import { renderModelList, disableChatControls } from "./models.js";

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
        // Use OpenAI-compatible endpoint for universal compatibility
        const data = await apiCall("/v1/models");
        state.connected = true;
        state.status = "connected";

        // Transform OpenAI model format to internal format
        state.models = (data.data || []).map(m => ({
            key: m.id,
            type: "llm",
            display_name: m.id,
            loaded_instances: [],
        }));

        // Validate saved selected model exists in the fetched list
        if (state.selectedModel && !state.models.some(m => m.key === state.selectedModel)) {
            state.selectedModel = null;
            saveSettings();
        }

        updateStatus(dom, true);
        dom.connectBtn.textContent = "Disconnect";
        showToast(`Connected - ${state.models.length} models found`, "success");

        renderModelList(dom);
        enableChatControls(dom);
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
 * @param {Object} dom - DOM element references.
 */
export function disconnect(dom) {
    state.connected = false;
    state.status = "disconnected";
    state.models = [];
    state.loadedModels.clear();
    state.selectedModel = null;
    state.chatMessages = [];
    state.metrics = { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 };
    dom.chatMessages.innerHTML = "";

    updateStatus(dom, false);
    dom.connectBtn.textContent = "Connect";
    dom.modelList.innerHTML = `<li class="empty-state" style="flex:unset;padding:20px 0;">
        <div class="empty-subtitle">Connect to an OpenAI-compatible endpoint to see available models</div>
    </li>`;
    disableChatControls(dom);
    showToast("Disconnected", "info");
}

/**
 * Update the connection status indicator.
 * @param {Object} dom
 * @param {boolean|null} connected - true=connected, false=disconnected, null=loading state
 * @param {string} statusText - override text (e.g. "Connecting...")
 */
export function updateStatus(dom, connected, statusText = null) {
    if (connected === null) {
        // In-progress state (connecting, loading, unloading)
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
        m.type === "llm" &&
        (state.loadedModels.has(m.key) || (m.loaded_instances && m.loaded_instances.length > 0))
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
        dom.chatModelLabel.textContent = model?.display_name || state.selectedModel;
    } else if (hasChatMessages) {
        // Show existing chat in read-only mode (no model loaded)
        dom.emptyState.style.display = "none";
        dom.chatHeader.style.display = "flex";
        dom.chatMessages.style.display = "flex";
        dom.chatMetrics.style.display = "flex";

        if (state.selectedModel) {
            const model = state.models.find(m => m.key === state.selectedModel);
            dom.chatModelLabel.textContent = model?.display_name || state.selectedModel;
        }
    } else {
        dom.emptyState.style.display = "flex";
        dom.chatHeader.style.display = "none";
        dom.chatMessages.style.display = "none";
        dom.chatMetrics.style.display = "none";
    }
}
