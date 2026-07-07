/**
 * State management and localStorage persistence.
 */

const SETTINGS_KEY = "lm_console_settings";

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
}
