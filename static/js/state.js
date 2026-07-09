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
    };
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

/**
 * Save current chat as a session in history.
 */
export function saveCurrentSession() {
    if (state.chatMessages.length === 0) return;
    const session = {
        id: state.currentSessionId || crypto.randomUUID(),
        createdAt: new Date().toISOString(),
        model: state.selectedModel || null,
        messages: [...state.chatMessages],
        preview: state.chatMessages[0]?.content?.substring(0, 80) || "Chat session",
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
        }
    } catch (e) {
        // Ignore parse errors from corrupt localStorage
    }
    loadSessionHistory();
}
