/**
 * Named profiles: save/load/delete/modify snapshots of the connection and
 * chat settings.
 *
 * A profile captures the six user-configurable fields (endpoint, API
 * token, selected model, system prompt, temperature, tool-call toggle) so
 * an entire environment can be switched with one click. Profiles persist
 * in localStorage via state.js (key "lm_console_profiles").
 */

import { state, saveSettings, saveProfiles } from "./state.js";
import { showToast } from "./ui.js";
import { renderModelList } from "./models.js";
import { enableChatControls, disconnect } from "./connection.js";

/**
 * Render the profiles list in the sidebar.
 * @param {Object} dom - DOM element references.
 */
export function renderProfileList(dom) {
    const profileList = dom.profileList;
    if (!profileList) return;

    if (state.profiles.length === 0) {
        profileList.innerHTML = `<li class="empty-state" style="flex:unset;padding:20px 0;">
            <div class="empty-subtitle">Saved profiles will appear here</div>
        </li>`;
        return;
    }

    // Static markup only — user-supplied names, endpoints, and model keys
    // are injected via textContent below, which is immune to HTML
    // injection (no escapeHtml import in this module's fixed import set).
    profileList.innerHTML = state.profiles.map((profile, index) => `
        <li class="profile-item" data-index="${index}">
            <div class="profile-info">
                <div class="profile-name"></div>
                <div class="profile-meta"></div>
            </div>
            <div class="profile-actions">
                <button class="profile-btn load-btn" data-action="load">Load</button>
                <button class="profile-btn delete-btn" data-action="delete">Delete</button>
            </div>
        </li>`).join("");

    // Fill in user content and bind event handlers
    profileList.querySelectorAll(".profile-item").forEach((item, index) => {
        const profile = state.profiles[index];
        if (!profile) return;

        item.querySelector(".profile-name").textContent = profile.name;
        item.querySelector(".profile-meta").textContent =
            (profile.endpoint || "—") + (profile.selectedModel ? " · " + profile.selectedModel : "");

        // Load button
        const loadBtn = item.querySelector(".load-btn");
        loadBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            loadProfile(dom, profile.name);
        });

        // Delete button
        const deleteBtn = item.querySelector(".delete-btn");
        deleteBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteProfile(dom, profile.name);
        });
    });
}

/**
 * Save (or modify) a profile under the given name, snapshotting the six
 * settings fields. Upsert by name: re-saving an existing name replaces
 * that entry in place ("modify"), a new name is added to the front.
 * @param {Object} dom - DOM element references.
 */
export function saveProfile(dom) {
    const name = dom.profileName.value.trim();
    if (!name) {
        showToast("Profile name is required", "error");
        return;
    }

    const profile = {
        name,
        endpoint: state.endpoint,
        apiToken: state.apiToken,
        selectedModel: state.selectedModel,
        systemPrompt: state.systemPrompt,
        temperature: state.temperature,
        toolCallEnabled: state.toolCallEnabled,
        savedAt: new Date().toISOString(),
    };

    const existingIndex = state.profiles.findIndex(p => p.name === name);
    if (existingIndex >= 0) {
        // Modify: replace in place, keeping the profile's position.
        state.profiles[existingIndex] = profile;
    } else {
        // New profile: newest first.
        state.profiles.unshift(profile);
    }

    saveProfiles();
    renderProfileList(dom);
    showToast(`Profile "${name}" saved`, "success");
}

/**
 * Load a profile: apply its six fields to state and the settings DOM
 * controls (including the "use server default" toggles) and persist.
 *
 * When connected with an unchanged endpoint/apiToken, the model list is
 * re-rendered and chat controls are re-evaluated; a profile model that is
 * no longer in the live list is dropped (selection nulled). When connected
 * but the profile points at a different endpoint/apiToken, the live
 * connection is bound to the OLD endpoint (heartbeat, model list,
 * isLmStudioEndpoint, load/unload button visibility) and would silently go
 * stale — so the connection is dropped and the user is prompted to
 * reconnect; the profile's model selection is re-applied afterwards
 * (disconnect() resets it) so it survives to the next connect.
 * @param {Object} dom - DOM element references.
 * @param {string} name - Profile name to load.
 */
export function loadProfile(dom, name) {
    const profile = state.profiles.find(p => p.name === name);
    if (!profile) {
        showToast("Profile not found", "error");
        return;
    }

    // Capture the connection values before applying: loading a profile
    // that points elsewhere while connected must disconnect (see below).
    const prevEndpoint = state.endpoint;
    const prevApiToken = state.apiToken;

    // Apply to state (empty string / null = unset → server default)
    state.endpoint = profile.endpoint;
    state.apiToken = profile.apiToken;
    state.selectedModel = profile.selectedModel;
    state.systemPrompt = profile.systemPrompt || "";
    state.temperature = (typeof profile.temperature === "number") ? profile.temperature : null;
    state.toolCallEnabled = !!profile.toolCallEnabled;

    // Apply to the connection DOM controls
    dom.endpoint.value = state.endpoint;
    dom.apiToken.value = state.apiToken;

    // System prompt + "use server default" toggle
    const promptUnset = state.systemPrompt === "";
    dom.systemPromptUnset.checked = promptUnset;
    dom.systemPrompt.value = state.systemPrompt;
    dom.systemPrompt.disabled = promptUnset;

    // Temperature + "use server default" toggle
    const tempUnset = state.temperature === null;
    dom.temperatureUnset.checked = tempUnset;
    dom.temperature.disabled = tempUnset;
    if (tempUnset) {
        dom.temperatureValue.textContent = "—";
    } else {
        dom.temperature.value = state.temperature;
        dom.temperatureValue.textContent = state.temperature.toFixed(2);
    }

    // Tool call toggle
    dom.toolCallToggle.checked = state.toolCallEnabled;

    saveSettings();

    if (state.connected) {
        if (state.endpoint !== prevEndpoint || state.apiToken !== prevApiToken) {
            // Endpoint/apiToken changed: tear down the connection bound to
            // the old endpoint instead of leaving heartbeat/model list/
            // isLmStudioEndpoint pointing at it.
            disconnect(dom);
            // disconnect() resets state.selectedModel; re-apply the
            // profile's model so the selection survives the teardown and
            // is still in effect when the user reconnects (connect()
            // re-validates it against the live model list and persists it).
            state.selectedModel = profile.selectedModel;
            showToast("Reconnect to apply endpoint changes", "info");
        } else {
            // Same endpoint: refresh model list + chat availability for the
            // restored model/settings. If the profile's model is no longer
            // in the live list (e.g. unloaded or renamed since the profile
            // was saved), drop the selection so chat can't target a model
            // the endpoint no longer offers.
            if (state.selectedModel && !state.models.some(m => m.key === state.selectedModel)) {
                state.selectedModel = null;
                saveSettings();
            }
            renderModelList(dom);
            enableChatControls(dom);
        }
    }

    showToast(`Profile "${name}" loaded`, "success");
}

/**
 * Delete a profile by name.
 * @param {Object} dom - DOM element references.
 * @param {string} name - Profile name to delete.
 */
export function deleteProfile(dom, name) {
    const index = state.profiles.findIndex(p => p.name === name);
    if (index === -1) {
        showToast("Profile not found", "error");
        return;
    }

    state.profiles.splice(index, 1);
    saveProfiles();
    renderProfileList(dom);
    showToast(`Profile "${name}" deleted`, "info");
}
