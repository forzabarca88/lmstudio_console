/**
 * State management and localStorage persistence.
 */

const SETTINGS_KEY = "lm_console_settings";
const HISTORY_KEY = "lm_console_history";

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
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
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
        messages: [...state.chatMessages],
        preview,
    };
    // Remove existing session with same id (if continuing)
    state.sessionHistory = state.sessionHistory.filter(s => s.id !== session.id);
    // Add to front
    state.sessionHistory.unshift(session);
    // Keep last 10
    if (state.sessionHistory.length > 10) {
        state.sessionHistory = state.sessionHistory.slice(0, 10);
    }
    state.currentSessionId = session.id;
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.sessionHistory));
}

/**
 * Load session history from localStorage.
 */
export function loadSessionHistory() {
    try {
        const saved = JSON.parse(localStorage.getItem(HISTORY_KEY));
        state.sessionHistory = saved || [];
    } catch {
        state.sessionHistory = [];
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
 * Save session history to localStorage.
 */
export function saveSessionHistory() {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.sessionHistory));
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
