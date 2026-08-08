/**
 * Chat session history: render, delete, continue sessions.
 */

import { state, saveSettings, saveSessionHistory, saveCurrentSession } from "./state.js";
import { showToast } from "./ui.js";
import { enableChatControls } from "./connection.js";
import { appendMessage, cancelAndResetUI } from "./chat.js";

/**
 * Render the session history list in the sidebar.
 * @param {Object} dom - DOM element references.
 */
export function renderHistoryList(dom) {
    const historyList = dom.historyList;
    if (!historyList) return;

    if (state.sessionHistory.length === 0) {
        historyList.innerHTML = `<li class="empty-state" style="flex:unset;padding:20px 0;">
            <div class="empty-subtitle">Chat sessions will appear here</div>
        </li>`;
        return;
    }

    historyList.innerHTML = state.sessionHistory.map((session, index) => {
        const date = new Date(session.createdAt);
        const timeStr = date.toLocaleDateString() + " " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        const msgCount = session.messages?.length || 0;

        return `<li class="history-item" data-id="${session.id}">
            <div class="history-info">
                <div class="history-preview">${escapeHtml(session.preview)}</div>
                <div class="history-meta">${timeStr} · ${msgCount} messages${session.model ? ' · ' + escapeHtml(session.model) : ''}</div>
            </div>
            <div class="history-actions">
                <button class="history-btn continue-btn" data-action="continue">Continue</button>
                <button class="history-btn delete-btn" data-action="delete">Delete</button>
            </div>
        </li>`;
    }).join("");

    // Bind event handlers
    historyList.querySelectorAll(".history-item").forEach(item => {
        item.addEventListener("click", (e) => {
            // Don't trigger if clicking a button
            if (e.target.closest(".history-btn")) return;
            const id = item.dataset.id;
            continueSession(dom, id);
        });

        // Continue button
        const continueBtn = item.querySelector(".continue-btn");
        continueBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const id = item.dataset.id;
            continueSession(dom, id);
        });

        // Delete button
        const deleteBtn = item.querySelector(".delete-btn");
        deleteBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const id = item.dataset.id;
            deleteSession(dom, id);
        });
    });
}

/**
 * Continue a saved session — restore its messages into the current chat.
 * @param {Object} dom - DOM element references.
 * @param {string} sessionId - Session id to restore.
 */
export function continueSession(dom, sessionId) {
    // Cancel any active in-flight request and reset the streaming UI before
    // switching session state, so the stop button and "Generating..."
    // indicator don't linger on the restored view while the aborted fetch
    // settles. (abortActiveRequest alone leaves those visible.)
    cancelAndResetUI(dom);

    const session = state.sessionHistory.find(s => s.id === sessionId);
    if (!session) {
        showToast("Session not found", "error");
        return;
    }

    // Save current chat before switching
    if (state.chatMessages.length > 0) {
        saveCurrentSession();
    }

    // Restore the session
    state.chatMessages = [...session.messages];
    state.currentSessionId = session.id;
    state.selectedModel = session.model || state.selectedModel;
    state.metrics = { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 };
    state.attachments = [];

    // Render messages
    dom.chatMessages.innerHTML = "";
    for (const msg of state.chatMessages) {
        appendMessage(dom, msg, msg.role);
    }

    // Show chat UI
    dom.emptyState.style.display = "none";
    dom.chatHeader.style.display = "flex";
    dom.chatMessages.style.display = "flex";
    dom.chatMetrics.style.display = "flex";

    const model = state.models.find(m => m.key === state.selectedModel);
    if (dom.chatModelLabel) {
        dom.chatModelLabel.textContent = model?.display_name || state.selectedModel || "—";
    }

    // Enable/disable input based on whether model is loaded
    const hasLoadedModel = state.loadedModels.size > 0 && state.selectedModel &&
        state.models.some(m => m.key === state.selectedModel && m.loaded_instances && m.loaded_instances.length > 0);
    dom.chatInput.disabled = !hasLoadedModel;
    dom.sendBtn.disabled = !hasLoadedModel;
    if (dom.attachBtn) dom.attachBtn.disabled = !hasLoadedModel;

    saveSettings();
    showToast("Session restored", "success");
}

/**
 * Delete a session from history.
 * @param {Object} dom - DOM element references.
 * @param {string} sessionId - Session id to delete.
 */
export function deleteSession(dom, sessionId) {
    // If deleting the currently active session while a request is in flight,
    // cancel it (and reset the streaming UI) so the backend stream is torn
    // down server-side rather than running orphaned.
    if (sessionId === state.currentSessionId) {
        cancelAndResetUI(dom);
    }

    const session = state.sessionHistory.find(s => s.id === sessionId);
    if (!session) {
        showToast("Session not found", "error");
        return;
    }

    // If deleting the current session, clear current chat
    if (state.currentSessionId === sessionId) {
        state.chatMessages = [];
        state.currentSessionId = null;
        dom.chatMessages.innerHTML = "";
        dom.emptyState.style.display = "flex";
        dom.chatHeader.style.display = "none";
        dom.chatMessages.style.display = "none";
        dom.chatMetrics.style.display = "none";
        dom.chatInput.disabled = true;
        dom.sendBtn.disabled = true;
        if (dom.attachBtn) dom.attachBtn.disabled = true;
    }

    state.sessionHistory = state.sessionHistory.filter(s => s.id !== sessionId);
    saveSessionHistory();
    renderHistoryList(dom);
    showToast("Session deleted", "info");
}

/**
 * Escape HTML special characters.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}
