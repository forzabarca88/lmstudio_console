/**
 * State management and localStorage persistence.
 */

const SETTINGS_KEY = "lm_console_settings";
const HISTORY_KEY = "lm_console_history";

// Maximum number of chat sessions to keep in history (SPEC: at least 10).
const HISTORY_MAX_SESSIONS = 10;

/**
 * Write a value to localStorage, reporting why it failed.
 * @returns {"ok"|"quota"|"error"} "quota" when the browser storage limit
 *   was hit (QuotaExceededError), "error" for any other failure.
 */
function writeStorage(key, value) {
    try {
        localStorage.setItem(key, value);
        return "ok";
    } catch (e) {
        if (isStorageQuotaError(e)) return "quota";
        console.error(`Failed to write "${key}" to localStorage:`, e);
        return "error";
    }
}

/**
 * Detect quota-exceeded storage errors across engines.
 * @param {unknown} e - The caught error.
 * @returns {boolean}
 */
function isStorageQuotaError(e) {
    return !!e && (
        e.name === "QuotaExceededError" ||          // Chromium, Firefox, Safari (modern)
        e.name === "NS_ERROR_DOM_QUOTA_REACHED" ||   // Firefox (legacy)
        e.code === 22                                 // DOMException QUOTA_EXCEEDED_ERR
    );
}

/**
 * Notify the UI (app.js) about a persistence problem. The listener shows a
 * toast and re-renders the history list when sessions were dropped.
 * @param {Object} detail - { message, historyChanged? }
 */
function dispatchStorageWarning(detail) {
    window.dispatchEvent(new CustomEvent("lmconsole:storage-warning", { detail }));
}

/**
 * Replace inlined media data URLs with lightweight text placeholders.
 *
 * Live multimodal messages embed the full base64 payload of every attached
 * image, audio, or file (easily megabytes). Persisting those to
 * localStorage exhausts the browser's ~5MB origin quota, so stored sessions
 * carry a placeholder instead. The in-memory session keeps the full payload
 * for the live conversation; only the persisted copy is reduced.
 * Consequence: continuing a multimodal session later restores the
 * placeholder text, not the media.
 * @param {Array} messages - OpenAI-compatible messages.
 * @returns {Array} Shallow-copied messages safe to persist.
 */
function sanitizeMessagesForStorage(messages) {
    if (!Array.isArray(messages)) return [];
    return messages.map((msg) => {
        if (!msg || !Array.isArray(msg.content)) return { ...msg };
        const content = msg.content.map((part) => {
            if (part && part.type === "image_url") {
                const url = part.image_url && part.image_url.url;
                if (typeof url === "string" && url.startsWith("data:")) {
                    const mime = url.slice(5).split(";")[0] || "image";
                    return { type: "text", text: `[image attached: ${mime}]` };
                }
            }
            if (part && (part.type === "input_audio" || part.type === "input_file")) {
                const data = part.file_data || part.file;
                if (typeof data === "string" && data.startsWith("data:")) {
                    const mime = data.slice(5).split(";")[0] || part.type;
                    return { type: "text", text: `[${part.type} attached: ${mime}]` };
                }
            }
            return part;
        });
        return { ...msg, content };
    });
}

/**
 * Persist session history to localStorage, recovering from the browser
 * quota limit.
 *
 * On QuotaExceededError the oldest sessions are dropped one at a time until
 * the write fits (recent history is preserved). The previously stored value
 * is freed first so a smaller candidate can actually be written when the
 * origin is at its limit. If even an empty history does not fit, the key is
 * cleared. Any trimming is surfaced via the "lmconsole:storage-warning"
 * event (app.js shows a toast and re-renders the history list).
 */
function persistHistory() {
    let history = [...state.sessionHistory];
    let freed = false;

    const tryWrite = (candidate) => {
        let result = writeStorage(HISTORY_KEY, JSON.stringify(candidate));
        if (result === "quota" && !freed) {
            // Free the space held by the previously stored (larger) value
            // before retrying with a smaller candidate.
            try {
                localStorage.removeItem(HISTORY_KEY);
                freed = true;
            } catch {
                // ignore - a failed removeItem cannot help anyway
            }
            result = writeStorage(HISTORY_KEY, JSON.stringify(candidate));
        }
        return result;
    };

    let dropped = 0;
    let result = tryWrite(history);
    while (result === "quota" && history.length > 0) {
        history.pop();
        dropped++;
        result = tryWrite(history);
    }

    if (result === "ok") {
        state.sessionHistory = history;
        if (dropped > 0) {
            dispatchStorageWarning({
                message: history.length === 0
                    ? "Browser storage is full — chat history could not be saved."
                    : `Chat storage is full — ${dropped} older session${dropped === 1 ? "" : "s"} removed.`,
                historyChanged: true,
            });
        }
        return;
    }

    if (result === "error") {
        // Non-quota failure: storage is unavailable (private mode, disabled
        // storage). Keep the in-memory history; writes will keep failing.
        console.error("Session history could not be persisted (storage unavailable)");
        return;
    }

    // Even an empty history did not fit: clear the key and start fresh.
    // Trade-off: the quota may be held by *other* keys, in which case
    // dropping the stored history reclaims nothing and previously
    // persistable sessions are lost. The in-memory copy is reset anyway so
    // state matches what is actually on disk, and the toast notifies the
    // user instead of failing silently.
    try {
        localStorage.removeItem(HISTORY_KEY);
    } catch {
        // ignore
    }
    state.sessionHistory = [];
    dispatchStorageWarning({
        message: "Browser storage is full — chat history could not be saved.",
        historyChanged: true,
    });
}


export const state = {
    endpoint: "http://localhost:1234",
    apiToken: "",
    connected: false,
    status: "disconnected", // disconnected | connecting | connected | loading | unloading | error
    models: [],
    loadedModels: new Set(),
    selectedModel: null,
    isLmStudioEndpoint: false, // true = LM Studio (models need loading), false = standard OpenAI
    chatMessages: [],
    systemPrompt: "You are a helpful assistant.",
    temperature: 0.7,
    streaming: false,
    // Chat metrics
    metrics: {
        tokensPerSecond: 0,
        timeToFirstToken: null,
        totalTokens: 0,
    },
    // Session history
    sessionHistory: [],
    currentSessionId: null,
    // Heartbeat
    heartbeatInterval: null,
    // Active request cancellation (runtime only, never persisted)
    abortController: null,
    // Why the active request was aborted: "stop" (stop button) or "navigate"
    // (new chat / continue session / disconnect). Used by sendMessage's
    // catch/finally to decide whether to preserve partial content and
    // whether to refocus the input. Cleared in sendMessage's finally.
    abortReason: null,
    // Theme
    theme: "cyberpunk",
    // Agentic tools
    toolCallEnabled: false,
    // File attachments for multimodal messages
    attachments: [],
};

/**
 * Save current settings to localStorage.
 */
export function saveSettings() {
    const settings = {
        endpoint: state.endpoint,
        apiToken: state.apiToken,
        systemPrompt: state.systemPrompt,
        temperature: state.temperature,
        selectedModel: state.selectedModel,
        toolCallEnabled: state.toolCallEnabled,
        theme: state.theme,
    };
    if (writeStorage(SETTINGS_KEY, JSON.stringify(settings)) === "quota") {
        dispatchStorageWarning({
            message: "Browser storage is full — settings may not be saved.",
        });
    }
}

/**
 * Generate a UUID v4 string.
 *
 * Prefers crypto.randomUUID(), but falls back to crypto.getRandomValues()
 * (available in insecure contexts) and finally Math.random(). This is
 * required because crypto.randomUUID() is only exposed in secure contexts
 * (HTTPS or localhost) — on plain-HTTP remote deployments it is undefined
 * and calling it throws "crypto.randomUUID is not a function".
 */
export function generateUuid() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    try {
        if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
            // Conforming browsers fill `bytes` in place and return it; some
            // polyfills return a new array instead. Handle both.
            const filled = crypto.getRandomValues(bytes);
            if (filled !== bytes && filled && filled.length >= 16) {
                for (let i = 0; i < 16; i++) bytes[i] = filled[i];
            }
        } else {
            for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
        }
    } catch {
        // getRandomValues may throw in exotic embedders (broken polyfill,
        // entropy exhaustion); fall through to Math.random.
        for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * Save current chat as a session in history.
 */
export function saveCurrentSession() {
    if (state.chatMessages.length === 0) return;

    // Extract text preview from potentially multimodal content
    let preview = "Chat session";
    const firstMsg = state.chatMessages[0];
    if (typeof firstMsg?.content === "string") {
        preview = firstMsg.content.substring(0, 80);
    } else if (Array.isArray(firstMsg?.content)) {
        const textParts = firstMsg.content.filter(c => c.type === "text").map(c => c.text);
        if (textParts.length > 0) {
            preview = textParts.join(" ").substring(0, 80);
        }
    }

    const session = {
        id: state.currentSessionId || generateUuid(),
        createdAt: new Date().toISOString(),
        model: state.selectedModel || null,
        // Persist a sanitized copy: live messages may contain megabytes of
        // base64 image data that would exceed the localStorage quota.
        messages: sanitizeMessagesForStorage(state.chatMessages),
        preview,
    };
    // Remove existing session with same id (if continuing)
    state.sessionHistory = state.sessionHistory.filter(s => s.id !== session.id);
    // Add to front
    state.sessionHistory.unshift(session);
    // Keep last N sessions
    if (state.sessionHistory.length > HISTORY_MAX_SESSIONS) {
        state.sessionHistory = state.sessionHistory.slice(0, HISTORY_MAX_SESSIONS);
    }
    state.currentSessionId = session.id;
    persistHistory();
}

/**
 * Load session history from localStorage.
 *
 * Sanitizes any image data URLs left behind by older versions (the main
 * cause of quota exhaustion) and re-persists the shrunken value so the
 * storage is reclaimed on the first load after an upgrade.
 */
export function loadSessionHistory() {
    let raw = null;
    try {
        raw = localStorage.getItem(HISTORY_KEY);
    } catch (e) {
        // Storage access itself can throw (disabled storage, some private
        // modes); degrade to an empty history like the parse-failure path.
        console.error("Failed to read session history from localStorage:", e);
        state.sessionHistory = [];
        return;
    }
    let saved = null;
    if (raw) {
        try {
            saved = JSON.parse(raw);
        } catch {
            saved = null;
        }
    }
    if (!Array.isArray(saved)) {
        state.sessionHistory = [];
        return;
    }
    state.sessionHistory = saved.map(s => ({
        ...s,
        messages: sanitizeMessagesForStorage(s.messages),
    }));
    const sanitized = JSON.stringify(state.sessionHistory);
    if (sanitized.length < raw.length) {
        persistHistory();
    }
}

/**
 * Abort the active in-flight request, if any.
 *
 * This only cancels the client-side fetch via the stored AbortController. It
 * intentionally does NOT touch `state.streaming`; the normal `sendMessage`
 * cleanup path is responsible for resetting streaming state once the aborted
 * fetch settles. `abortController` is runtime state and is never persisted.
 *
 * Sets `state.abortReason = "navigate"` ONLY when there is an in-flight
 * request (abortController non-null), so sendMessage's catch/finally can
 * distinguish navigation-driven aborts (new chat / continue session /
 * disconnect) from stop-button aborts (cancelRequest sets "stop") and discard
 * partial content / skip refocusing accordingly. When called while idle
 * (abortController null) this is a no-op and leaves abortReason untouched,
 * avoiding a stale "navigate" flag that would make a later normal
 * completion skip refocusing the input.
 */
export function abortActiveRequest() {
    // Only flag a navigation abort when there is actually an in-flight
    // request to abort. Setting abortReason unconditionally (even when
    // abortController is null) leaves a stale "navigate" flag behind; a
    // subsequent normal completion's finally would misread it as a
    // navigation abort and skip refocusing the input.
    if (!state.abortController) return;
    state.abortReason = "navigate";
    state.abortController.abort();
    state.abortController = null;
}

/**
 * Save session history to localStorage (quota-safe; see persistHistory).
 */
export function saveSessionHistory() {
    persistHistory();
}

/**
 * Load saved settings from localStorage and apply to state.
 * @param {Object} dom - DOM element references.
 */
export function loadSettings(dom) {
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
            if (saved.selectedModel) {
                state.selectedModel = saved.selectedModel;
            }
            if (saved.toolCallEnabled !== undefined) {
                state.toolCallEnabled = saved.toolCallEnabled;
                if (dom.toolCallToggle) {
                    dom.toolCallToggle.checked = saved.toolCallEnabled;
                }
            }
            if (saved.theme) {
                state.theme = saved.theme;
            }
        }
    } catch (e) {
        // Ignore parse errors from corrupt localStorage
    }
    loadSessionHistory();
}

/**
 * Theme-to-mermaid mapping.
 */
const MERMAID_THEMES = {
    cyberpunk: "dark",
    light: "default",
    warm: "neutral",
};

/**
 * Apply a UI theme: swap stylesheet, update state, persist, and reinit mermaid.
 * @param {string} themeName - Theme key: "cyberpunk", "light", or "warm".
 */
export function applyTheme(themeName) {
    if (!themeName) return;
    state.theme = themeName;

    // Swap the theme stylesheet
    const link = document.getElementById("theme-stylesheet");
    if (link) {
        link.href = `/static/css/theme-${themeName}.css`;
    }

    // Persist
    saveSettings();

    // Reinitialize mermaid with matching theme
    if (typeof mermaid !== "undefined") {
        mermaid.initialize({ theme: MERMAID_THEMES[themeName] || "dark" });
    }

    // Dispatch custom event for other modules
    window.dispatchEvent(new CustomEvent("themechanged", { detail: { theme: themeName } }));
}
