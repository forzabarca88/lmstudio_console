/**
 * Model management: list, refresh, load, unload models.
 */

import { state, saveSettings } from "./state.js";
import { apiCall } from "./api.js";
import { showToast, formatBytes, escapeHtml } from "./ui.js";
import { enableChatControls, updateStatus } from "./connection.js";

/**
 * Refresh the model list from the server.
 * @param {Object} dom - DOM element references.
 */
export async function refreshModels(dom) {
    if (!state.connected) {
        showToast("Not connected", "error");
        return;
    }

    state.status = "loading";
    updateStatus(dom, null, "Fetching models...");
    dom.refreshModelsBtn.textContent = "↻ Loading...";
    dom.refreshModelsBtn.disabled = true;

    try {
        const data = await apiCall("/api/v1/models");
        state.models = data.models || [];
        state.status = "connected";

        // Track which models are loaded
        state.loadedModels.clear();
        for (const model of state.models) {
            if (model.loaded_instances && model.loaded_instances.length > 0) {
                for (const inst of model.loaded_instances) {
                    state.loadedModels.add(inst.id);
                }
            }
        }

        renderModelList(dom);
        updateStatus(dom, true);
        showToast(`Found ${state.models.length} models`, "info");
    } catch (e) {
        state.status = "connected";
        updateStatus(dom, true);
        showToast(`Refresh failed: ${e.message}`, "error");
    }

    dom.refreshModelsBtn.textContent = "↻ Refresh";
    dom.refreshModelsBtn.disabled = false;
}

/**
 * Render the model list in the sidebar.
 * @param {Object} dom - DOM element references.
 */
export function renderModelList(dom) {
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
        const pubStr = model.publisher || "";
        const meta = [pubStr, paramsStr, quantStr, sizeStr].filter(Boolean).join(" · ");

        return `<li class="model-item ${isLoaded ? 'loaded' : ''} ${isSelected ? 'selected' : ''}"
                     data-key="${model.key}"
                     data-instance-id="${model.loaded_instances?.[0]?.id || model.key}">
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
            renderModelList(dom);
            enableChatControls(dom);
        });
    });
}

/**
 * Load the selected model.
 * @param {Object} dom - DOM element references.
 */
export async function loadModel(dom) {
    if (!state.selectedModel) {
        showToast("Select a model first", "error");
        return;
    }

    state.status = "loading";
    updateStatus(dom, null, "Loading model...");
    dom.loadModelBtn.textContent = "Loading...";
    dom.loadModelBtn.disabled = true;

    try {
        const data = await apiCall("/api/v1/models/load", "POST", {
            model: state.selectedModel,
            echo_load_config: true,
        });

        state.loadedModels.add(data.instance_id);
        state.status = "connected";
        renderModelList(dom);
        enableChatControls(dom);
        updateStatus(dom, true);
        showToast(`Model loaded in ${data.load_time_seconds?.toFixed(1) || '?'}s`, "success");
    } catch (e) {
        state.status = "connected";
        updateStatus(dom, true);
        showToast(`Load failed: ${e.message}`, "error");
    }

    dom.loadModelBtn.textContent = "Load";
    dom.loadModelBtn.disabled = false;
}

/**
 * Unload the selected model.
 * @param {Object} dom - DOM element references.
 */
export async function unloadModel(dom) {
    if (!state.selectedModel) {
        showToast("Select a model first", "error");
        return;
    }

    // Find the instance ID for this model
    const model = state.models.find(m => m.key === state.selectedModel);
    const instanceId = model?.loaded_instances?.[0]?.id || state.selectedModel;

    state.status = "unloading";
    updateStatus(dom, null, "Unloading model...");
    dom.unloadModelBtn.textContent = "Unloading...";
    dom.unloadModelBtn.disabled = true;

    try {
        await apiCall("/api/v1/models/unload", "POST", { instance_id: instanceId });

        state.loadedModels.delete(instanceId);
        state.selectedModel = null;
        state.chatMessages = [];
        dom.chatMessages.innerHTML = "";
        state.status = "connected";
        renderModelList(dom);
        disableChatControls(dom);
        updateStatus(dom, true);
        showToast("Model unloaded", "success");
    } catch (e) {
        state.status = "connected";
        updateStatus(dom, true);
        showToast(`Unload failed: ${e.message}`, "error");
    }

    dom.unloadModelBtn.textContent = "Unload";
    dom.unloadModelBtn.disabled = false;
}

/**
 * Disable chat controls (no model loaded).
 * @param {Object} dom
 */
export function disableChatControls(dom) {
    dom.chatInput.disabled = true;
    dom.sendBtn.disabled = true;
    dom.emptyState.style.display = "flex";
    dom.chatHeader.style.display = "none";
    dom.chatMessages.style.display = "none";
    if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";
    state.selectedModel = null;
    state.chatMessages = [];
    state.streaming = false;
}
