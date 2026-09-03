/**
 * Model management: list, refresh, load, unload models.
 * Works with OpenAI-compatible endpoints.
 */

import { state, saveSettings } from "./state.js";
import { apiCall } from "./api.js";
import { showToast, formatBytes, escapeHtml, updateMetrics } from "./ui.js";
import { enableChatControls, updateStatus } from "./connection.js";

/**
 * Sync loaded model state from the LM Studio native API.
 * Updates state.loadedModels and model.loaded_instances to reflect
 * what the server actually has loaded at this moment.
 * If the endpoint is not LM Studio (standard OpenAI), all models are
 * treated as available without loading.
 */
export async function syncLoadedModels() {
    try {
        const lmData = await apiCall("/api/v1/models");
        const lmModels = lmData.models || [];
        state.isLmStudioEndpoint = true;
        state.loadedModels.clear();
        for (const lmModel of lmModels) {
            const model = state.models.find(m => m.key === lmModel.key);
            if (model) {
                if (lmModel.loaded_instances && lmModel.loaded_instances.length > 0) {
                    model.loaded_instances = lmModel.loaded_instances;
                    for (const inst of lmModel.loaded_instances) {
                        state.loadedModels.add(inst.id);
                    }
                } else {
                    model.loaded_instances = [];
                }
            }
        }
    } catch {
        // Standard OpenAI-compatible endpoint — all models are always available
        state.isLmStudioEndpoint = false;
        for (const model of state.models) {
            // Mark each model as ready with a virtual instance so
            // enableChatControls can distinguish loaded vs unloaded.
            model.loaded_instances = [{ id: `direct-${model.key}` }];
            state.loadedModels.add(`direct-${model.key}`);
        }
    }
}

/**
 * Refresh the model list from the server using OpenAI-compatible endpoint.
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
        const data = await apiCall("/v1/models");
        // Transform OpenAI model format to internal format
        state.models = (data.data || []).map(m => ({
            key: m.id,
            type: "llm",
            display_name: m.id,
            loaded_instances: [],
        }));

        // Sync loaded state from the server
        await syncLoadedModels();

        state.status = "connected";
        renderModelList(dom);
        enableChatControls(dom);
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
            <div class="empty-subtitle">No models available</div>
        </li>`;
        return;
    }

    dom.modelList.innerHTML = llmModels.map(model => {
        const isLoaded = model.loaded_instances && model.loaded_instances.length > 0;
        const isSelected = state.selectedModel === model.key;
        // Only show "loaded" badge for LM Studio (where loading is explicit)
        const showLoadedBadge = state.isLmStudioEndpoint && isLoaded;

        return `<li class="model-item ${isLoaded ? 'loaded' : ''} ${isSelected ? 'selected' : ''}"
                     data-key="${escapeHtml(model.key)}"
                     data-instance-id="${escapeHtml(model.loaded_instances?.[0]?.id || model.key)}">
            <div class="model-info">
                <div class="model-name">${escapeHtml(model.display_name || model.key)}</div>
            </div>
            ${showLoadedBadge ? '<span class="model-badge badge-loaded">loaded</span>' : ''}
        </li>`;
    }).join("");

    // Click handlers for model items
    dom.modelList.querySelectorAll(".model-item").forEach(item => {
        item.addEventListener("click", () => {
            const key = item.dataset.key;
            state.selectedModel = key;
            saveSettings();
            renderModelList(dom);
            enableChatControls(dom);
        });
    });
}

/**
 * Load the selected model using LM Studio native API.
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
        // Update the model object so renderModelList sees the loaded state
        const model = state.models.find(m => m.key === state.selectedModel);
        if (model) {
            if (!model.loaded_instances) model.loaded_instances = [];
            model.loaded_instances.push({ id: data.instance_id });
        }
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
 * Unload the selected model using LM Studio native API.
 * Preserves existing chat messages per spec.
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
    const savedInstances = model?.loaded_instances ? [...model.loaded_instances] : null;

    state.status = "unloading";
    updateStatus(dom, null, "Unloading model...");
    dom.unloadModelBtn.textContent = "Unloading...";
    dom.unloadModelBtn.disabled = true;

    // Optimistically update UI immediately
    state.loadedModels.delete(instanceId);
    if (model) model.loaded_instances = [];
    renderModelList(dom);

    // Disable input but preserve chat messages
    dom.chatInput.disabled = true;
    dom.sendBtn.disabled = true;
    if (dom.attachBtn) dom.attachBtn.disabled = true;
    if (dom.streamingIndicator) dom.streamingIndicator.style.display = "none";

    // Show existing chat in read-only mode
    if (state.chatMessages.length > 0) {
        dom.emptyState.style.display = "none";
        dom.chatHeader.style.display = "flex";
        dom.chatMessages.style.display = "flex";
        dom.chatMetrics.style.display = "flex";
        const prevModel = state.models.find(m => m.key === state.selectedModel);
        if (dom.chatModelLabel) {
            dom.chatModelLabel.textContent = prevModel?.display_name || state.selectedModel;
        }
    } else {
        dom.emptyState.style.display = "flex";
        dom.chatHeader.style.display = "none";
        dom.chatMessages.style.display = "none";
        dom.chatMetrics.style.display = "none";
    }

    try {
        await apiCall("/api/v1/models/unload", "POST", { instance_id: instanceId });

        const prevSelectedModel = state.selectedModel;
        state.selectedModel = null;
        state.streaming = false;
        saveSettings();

        state.status = "connected";
        updateStatus(dom, true);
        showToast("Model unloaded", "success");
    } catch (e) {
        // Revert optimistic update on failure
        state.loadedModels.add(instanceId);
        if (model) model.loaded_instances = savedInstances;
        state.selectedModel = model.key;
        saveSettings();
        state.status = "connected";
        renderModelList(dom);
        enableChatControls(dom);
        updateStatus(dom, true);
        showToast(`Unload failed: ${e.message}`, "error");
    }

    dom.unloadModelBtn.textContent = "Unload";
    dom.unloadModelBtn.disabled = false;
}


