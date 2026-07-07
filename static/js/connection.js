/**
 * Connection management: connect, disconnect, status updates.
 */

import { state, saveSettings } from "./state.js";
import { apiCall } from "./api.js";
import { showToast } from "./ui.js";
import { renderModelList, disableChatControls } from "./models.js";

/**
 * Connect to the configured endpoint.
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
    updateStatus(dom, null, "connecting");
    dom.connectBtn.textContent = "Connecting...";
    dom.connectBtn.disabled = true;

    try {
        const data = await apiCall("/api/v1/models");
        state.connected = true;
        state.status = "connected";
        state.models = data.models || [];

        updateStatus(dom, true);
        dom.connectBtn.textContent = "Disconnect";
        showToast("Connected to LM Studio", "success");

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
    dom.chatMessages.innerHTML = "";

    updateStatus(dom, false);
    dom.connectBtn.textContent = "Connect";
    dom.modelList.innerHTML = `<li class="empty-state" style="flex:unset;padding:20px 0;">
        <div class="empty-subtitle">Connect to LM Studio to see available models</div>
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
