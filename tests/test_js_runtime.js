/**
 * Runtime JS tests using Node.js.
 *
 * Actually executes the JS modules to verify:
 * - State management works correctly
 * - API call functions handle errors properly
 * - UI utilities format data correctly
 * - No runtime errors
 *
 * Run: node tests/test_js_runtime.js
 */

import { strict as assert } from "node:assert";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const staticDir = join(__dirname, "..", "static", "js");

let testsPassed = 0;
let testsFailed = 0;

function pass(name) {
    testsPassed++;
    console.log(`  ✓ ${name}`);
}

function fail(name, error) {
    testsFailed++;
    console.error(`  ✗ ${name}: ${error.message}`);
}

async function runTest(name, fn) {
    try {
        await fn();
        pass(name);
    } catch (error) {
        fail(name, error);
    }
}

// ─── Load modules ───────────────────────────────────────────────

const stateModule = await import(join(staticDir, "state.js"));
const apiModule = await import(join(staticDir, "api.js"));
const uiModule = await import(join(staticDir, "ui.js"));

// history.js has a circular dependency with chat.js (chat.js imports history.js)
// and ES module exports are read-only, so we can't override imports.
// Instead, read the source and transform imports to use already-loaded modules.
const historySource = readFileSync(join(staticDir, "history.js"), "utf-8");

// Transform import statements to const assignments from loaded modules
const transformedHistory = historySource
    .replace(
        /import\s*{\s*state,\s*saveSettings,\s*saveSessionHistory,\s*saveCurrentSession(?:,\s*abortActiveRequest)?\s*}\s*from\s*"\.\/state\.js";?/,
        'const { state, saveSettings, saveSessionHistory, saveCurrentSession, abortActiveRequest } = stateModule;'
    )
    .replace(
        /import\s*{\s*showToast\s*,\s*escapeHtml\s*}\s*from\s*"\.\/ui\.js";?/,
        'const { showToast, escapeHtml } = uiModule;'
    )
    .replace(
        /import\s*{\s*enableChatControls\s*}\s*from\s*"\.\/connection\.js";?/,
        'const enableChatControls = () => {};'
    )
    .replace(
        /import\s*{\s*appendMessage(?:,\s*cancelAndResetUI)?\s*}\s*from\s*"\.\/chat\.js";?/,
        'const appendMessage = () => {}; const cancelAndResetUI = () => {};'
    )
    .replace(/export\s+function/g, 'function');

// Execute transformed source in a context with the loaded modules
const historyModule = new Function("stateModule", "uiModule", `
    "use strict";
    ${transformedHistory}
    return { renderHistoryList, continueSession, deleteSession };
`)(stateModule, uiModule);

// profiles.js is loaded the same way: transform imports to use the
// already-loaded state/ui modules, stub renderModelList /
// enableChatControls/disconnect (with global counters so tests can assert
// which connection path loadProfile took — the disconnect stub also
// mirrors the real connection.js disconnect() STATE resets so tests see
// the same state teardown the browser would), and wrap showToast to
// capture the toasts it shows (delegating to the real implementation).
const profilesSource = readFileSync(join(staticDir, "profiles.js"), "utf-8");

const transformedProfiles = profilesSource
    .replace(
        /import\s*{\s*state,\s*saveSettings,\s*saveProfiles\s*}\s*from\s*"\.\/state\.js";?/,
        'const { state, saveSettings, saveProfiles } = stateModule;'
    )
    .replace(
        /import\s*{\s*showToast\s*}\s*from\s*"\.\/ui\.js";?/,
        'const { showToast: _showToastReal } = uiModule; '
        + 'const showToast = (message, type) => { '
        + 'globalThis._profileToasts = globalThis._profileToasts || []; '
        + 'globalThis._profileToasts.push({ message, type: type || "info" }); '
        + '_showToastReal(message, type); '
        + '};'
    )
    .replace(
        /import\s*{\s*renderModelList\s*}\s*from\s*"\.\/models\.js";?/,
        'const renderModelList = () => { globalThis._profileModelListCalls = (globalThis._profileModelListCalls || 0) + 1; };'
    )
    .replace(
        /import\s*{\s*enableChatControls,\s*disconnect\s*}\s*from\s*"\.\/connection\.js";?/,
        'const enableChatControls = () => { globalThis._profileEnableChatCalls = (globalThis._profileEnableChatCalls || 0) + 1; }; '
        + 'const disconnect = () => { '
        + 'globalThis._profileDisconnectCalls = (globalThis._profileDisconnectCalls || 0) + 1; '
        // Mirror the STATE resets of the real connection.js disconnect()
        // (DOM side effects are not needed in Node).
        + 'state.connected = false; '
        + 'state.status = "disconnected"; '
        + 'state.models = []; '
        + 'state.loadedModels.clear(); '
        + 'state.selectedModel = null; '
        + 'state.isLmStudioEndpoint = false; '
        + '};'
    )
    .replace(/export\s+function/g, 'function');

const profilesModule = new Function("stateModule", "uiModule", `
    "use strict";
    ${transformedProfiles}
    return { renderProfileList, saveProfile, loadProfile, deleteProfile };
`)(stateModule, uiModule);

// Mock browser globals for Node.js
globalThis.localStorage = {
    _data: {},
    getItem(key) { return this._data[key] || null; },
    setItem(key, value) { this._data[key] = String(value); },
    removeItem(key) { delete this._data[key]; },
    clear() { this._data = {}; },
};

// Track dispatched events for testing
globalThis._dispatchedEvents = [];

// Cache for mock DOM elements so properties (href, value, etc.) persist
const _domCache = {};

globalThis.document = {
    getElementById(id) {
        if (!_domCache[id]) {
            _domCache[id] = _createMockElement();
            _domCache[id].id = id;
        }
        return _domCache[id];
    },
    createElement(tag) {
        const el = _createMockElement();
        el.tagName = tag;
        return el;
    },
    body: { appendChild() {} },
};

// Reset the DOM cache before each test group
function _resetDomCache() {
    for (const key of Object.keys(_domCache)) delete _domCache[key];
}

// Mock window for event dispatching
globalThis.window = {
    addEventListener(type, fn) {
        globalThis._listenerMap = globalThis._listenerMap || {};
        if (!globalThis._listenerMap[type]) globalThis._listenerMap[type] = [];
        globalThis._listenerMap[type].push(fn);
    },
    dispatchEvent(event) {
        const handlers = (globalThis._listenerMap || {})[event.type] || [];
        for (const fn of handlers) fn(event);
    },
};
globalThis.CustomEvent = class {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};

function _createMockElement() {
    let _textContent = "";
    let _href = "";
    return {
        get textContent() { return _textContent; },
        set textContent(v) { _textContent = v; },
        get innerHTML() {
            // In real browsers, innerHTML returns HTML-escaped version of textContent
            return _textContent
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/'/g, '&#39;')
                .replace(/"/g, '&quot;');
        },
        set innerHTML(v) { _textContent = v; },
        get href() { return _href; },
        set href(v) { _href = v; },
        style: {},
        classList: { add() {}, remove() {}, toggle() {} },
        value: "",
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
        closest() { return null; },
        dataset: {},
        appendChild() {},
        remove() {},
    };
}

Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText() { return Promise.resolve(); } } },
    writable: true,
    configurable: true,
});

// Mock marked
globalThis.marked = { parse: (text) => `<p>${text}</p>`, setOptions: () => {} };

// Mock mermaid
globalThis.mermaid = { initialize: () => {}, render: async () => ({ svg: "<svg></svg>" }) };

// Mock crypto with counter to ensure unique IDs
let _uuidCounter = 0;
Object.defineProperty(globalThis, 'crypto', {
    value: { randomUUID: () => `test-uuid-${++_uuidCounter}` },
    writable: true,
    configurable: true,
});

// Mock AbortController global — required by cancellation tests. Some Node.js
// versions lack a global AbortController; provide a minimal mock when absent.
// The mock (and the native, when present) expose signal.aborted and the
// 'abort' event, which is all abortActiveRequest() relies on.
if (typeof globalThis.AbortController === 'undefined') {
    globalThis.AbortController = class {
        constructor() {
            this.signal = {
                aborted: false,
                _listeners: [],
                addEventListener(type, fn) {
                    if (type === 'abort') this._listeners.push(fn);
                },
                removeEventListener(type, fn) {
                    this._listeners = this._listeners.filter(f => f !== fn);
                },
            };
        }
        abort() {
            if (this.signal.aborted) return;
            this.signal.aborted = true;
            for (const fn of this.signal._listeners) {
                try { fn(); } catch {}
            }
        }
    };
}

// ─── State tests ────────────────────────────────────────────────

console.log("\nState module:");

await runTest("state exists with defaults", () => {
    const s = stateModule.state;
    assert.equal(s.endpoint, "http://localhost:1234");
    assert.equal(s.connected, false);
    assert.equal(s.systemPrompt, "", "systemPrompt defaults to unset (empty string)");
    assert.equal(s.temperature, null, "temperature defaults to unset (null)");
    assert.deepEqual(s.metrics, { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 });
    assert.equal(s.toolCallEnabled, false);
    assert.deepEqual(s.attachments, []);
    assert.equal(s.sidebarCollapsed, null, "sidebarCollapsed defaults to null (never toggled)");
});

await runTest("generateUuid falls back without crypto.randomUUID", () => {
    // Simulate an insecure context (plain-HTTP remote deployment) where
    // crypto.randomUUID is not a function.
    const originalCrypto = globalThis.crypto;
    let bytesSeed = 0;
    Object.defineProperty(globalThis, 'crypto', {
        value: { getRandomValues: (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = (bytesSeed + i) & 0xff; bytesSeed++; } },
        writable: true,
        configurable: true,
    });
    try {
        const id = stateModule.generateUuid();
        assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
        assert.notEqual(id, stateModule.generateUuid());
    } finally {
        Object.defineProperty(globalThis, 'crypto', { value: originalCrypto, writable: true, configurable: true });
    }
});

await runTest("generateUuid works with no crypto at all", () => {
    // Final fallback tier: crypto entirely unavailable (very exotic
    // embedder). generateUuid() must still produce a valid UUID.
    const originalCrypto = globalThis.crypto;
    Object.defineProperty(globalThis, 'crypto', { value: undefined, writable: true, configurable: true });
    try {
        const id = stateModule.generateUuid();
        assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    } finally {
        Object.defineProperty(globalThis, 'crypto', { value: originalCrypto, writable: true, configurable: true });
    }
});

await runTest("saveSettings persists to localStorage", () => {
    globalThis.localStorage.clear();
    stateModule.state.endpoint = "http://test:9999";
    stateModule.state.systemPrompt = "Test prompt";
    stateModule.state.temperature = 0.5;
    stateModule.state.toolCallEnabled = true;
    stateModule.state.selectedModel = "test-model";

    // Never toggled: the key must be absent from persisted settings
    stateModule.state.sidebarCollapsed = null;
    stateModule.saveSettings();
    const savedDefault = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.equal("sidebarCollapsed" in savedDefault, false, "sidebarCollapsed omitted when null");

    // Explicit choice: round-trips through the persisted JSON
    stateModule.state.sidebarCollapsed = true;
    stateModule.saveSettings();

    const saved = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.equal(saved.endpoint, "http://test:9999");
    assert.equal(saved.systemPrompt, "Test prompt");
    assert.equal(saved.temperature, 0.5);
    assert.equal(saved.toolCallEnabled, true);
    assert.equal(saved.selectedModel, "test-model");
    assert.equal(saved.sidebarCollapsed, true);
});

await runTest("loadSettings restores from localStorage", () => {
    globalThis.localStorage.clear();
    globalThis.localStorage.setItem("lm_console_settings", JSON.stringify({
        endpoint: "http://restored:8888",
        apiToken: "secret",
        systemPrompt: "Restored prompt",
        temperature: 0.3,
        selectedModel: "restored-model",
        toolCallEnabled: true,
        sidebarCollapsed: false,
    }));

    // Reset state
    stateModule.state.endpoint = "http://localhost:1234";
    stateModule.state.systemPrompt = "";
    stateModule.state.temperature = null;
    stateModule.state.toolCallEnabled = false;
    stateModule.state.selectedModel = null;
    stateModule.state.sidebarCollapsed = null;

    const dom = {
        endpoint: { value: "" },
        apiToken: { value: "" },
        systemPrompt: { value: "", disabled: false },
        systemPromptUnset: { checked: false },
        temperature: { value: "", disabled: false },
        temperatureUnset: { checked: false },
        temperatureValue: { textContent: "" },
        toolCallToggle: { checked: false },
        // classList is a no-op in the mock — assert on state, not classes
        sidebar: { classList: { toggle() {} } },
    };
    stateModule.loadSettings(dom);

    assert.equal(stateModule.state.endpoint, "http://restored:8888");
    assert.equal(stateModule.state.apiToken, "secret");
    assert.equal(stateModule.state.systemPrompt, "Restored prompt");
    assert.equal(stateModule.state.temperature, 0.3);
    assert.equal(stateModule.state.toolCallEnabled, true);
    assert.equal(stateModule.state.sidebarCollapsed, false);
    // Set values restore the controls as enabled (unset checkboxes off)
    assert.equal(dom.systemPrompt.value, "Restored prompt");
    assert.equal(dom.systemPromptUnset.checked, false);
    assert.equal(dom.systemPrompt.disabled, false);
    assert.equal(dom.temperature.value, 0.3);
    assert.equal(dom.temperatureValue.textContent, "0.30");
    assert.equal(dom.temperatureUnset.checked, false);
    assert.equal(dom.temperature.disabled, false);
});

await runTest("saveSettings persists unset systemPrompt and temperature", () => {
    globalThis.localStorage.clear();
    stateModule.state.systemPrompt = "";
    stateModule.state.temperature = null;

    stateModule.saveSettings();

    const saved = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.ok("systemPrompt" in saved, "systemPrompt key must be present (empty string, not dropped)");
    assert.ok("temperature" in saved, "temperature key must be present (null, not dropped)");
    assert.equal(saved.systemPrompt, "");
    assert.equal(saved.temperature, null);
});

await runTest("loadSettings with stored unset values restores unset state and disabled controls", () => {
    globalThis.localStorage.clear();
    globalThis.localStorage.setItem("lm_console_settings", JSON.stringify({
        endpoint: "http://localhost:1234",
        systemPrompt: "",
        temperature: null,
    }));

    // Reset state to set values
    stateModule.state.systemPrompt = "Custom prompt";
    stateModule.state.temperature = 0.9;

    const dom = {
        endpoint: { value: "" },
        apiToken: { value: "" },
        systemPrompt: { value: "Custom prompt", disabled: false },
        systemPromptUnset: { checked: false },
        temperature: { value: "0.9", disabled: false },
        temperatureUnset: { checked: false },
        temperatureValue: { textContent: "0.90" },
        toolCallToggle: { checked: false },
    };
    stateModule.loadSettings(dom);

    assert.equal(stateModule.state.systemPrompt, "");
    assert.equal(stateModule.state.temperature, null);
    assert.equal(dom.systemPrompt.value, "");
    assert.equal(dom.systemPromptUnset.checked, true, "prompt unset checkbox checked");
    assert.equal(dom.systemPrompt.disabled, true, "prompt textarea disabled");
    assert.equal(dom.temperatureUnset.checked, true, "temperature unset checkbox checked");
    assert.equal(dom.temperature.disabled, true, "temperature slider disabled");
    assert.equal(dom.temperatureValue.textContent, "—");
});

await runTest("saveCurrentSession handles multimodal content", () => {
    globalThis.localStorage.clear();
    stateModule.state.chatMessages = [
        { role: "user", content: [{ type: "text", text: "Hello with image" }, { type: "image_url", image_url: { url: "data:..." } }] },
        { role: "assistant", content: "Hi there!" },
    ];
    stateModule.state.selectedModel = "test-model";
    stateModule.state.currentSessionId = null;

    stateModule.saveCurrentSession();

    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 1);
    assert.equal(history[0].preview, "Hello with image");
    assert.equal(history[0].model, "test-model");
    assert.equal(history[0].messages.length, 2);
});

await runTest("saveCurrentSession caps history at 10 sessions", () => {
    globalThis.localStorage.clear();
    // Pre-populate with 9 sessions
    const existing = [];
    for (let i = 0; i < 9; i++) {
        existing.push({
            id: `session-${i}`,
            createdAt: new Date().toISOString(),
            model: "model-a",
            messages: [{ role: "user", content: `msg ${i}` }],
            preview: `msg ${i}`,
        });
    }
    stateModule.state.sessionHistory = existing;
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(existing));

    // Save one more — should push to 10
    stateModule.state.chatMessages = [
        { role: "user", content: "Tenth message" },
    ];
    stateModule.state.selectedModel = "model-b";
    stateModule.state.currentSessionId = null;
    stateModule.saveCurrentSession();

    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 10);
    assert.equal(history[0].preview, "Tenth message");

    // Save one more — should cap at 10, dropping the oldest
    stateModule.state.chatMessages = [
        { role: "user", content: "Eleventh message" },
    ];
    stateModule.state.currentSessionId = null;
    stateModule.saveCurrentSession();

    const history2 = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history2.length, 10);
    assert.equal(history2[0].preview, "Eleventh message");
    // After capping: Eleventh, Tenth, session-0..session-7 (session-8 dropped)
    assert.equal(history2[9].id, "session-7");
    // session-8 was dropped
    const ids = history2.map(s => s.id);
    assert.ok(!ids.includes("session-8"), "session-8 should have been dropped");
});

await runTest("saveCurrentSession skips when chat is empty", () => {
    globalThis.localStorage.clear();
    stateModule.state.sessionHistory = [{
        id: "existing-session",
        createdAt: new Date().toISOString(),
        model: "model-a",
        messages: [{ role: "user", content: "existing" }],
        preview: "existing",
    }];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(stateModule.state.sessionHistory));

    stateModule.state.chatMessages = [];
    stateModule.saveCurrentSession();

    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 1);
    assert.equal(history[0].id, "existing-session");
});

await runTest("saveCurrentSession updates existing session id", () => {
    globalThis.localStorage.clear();
    const sessionId = "existing-session-id";
    stateModule.state.sessionHistory = [{
        id: sessionId,
        createdAt: new Date().toISOString(),
        model: "model-a",
        messages: [{ role: "user", content: "old message" }],
        preview: "old message",
    }];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(stateModule.state.sessionHistory));

    stateModule.state.chatMessages = [
        { role: "user", content: "old message" },
        { role: "assistant", content: "old reply" },
        { role: "user", content: "new message" },
    ];
    stateModule.state.selectedModel = "model-a";
    stateModule.state.currentSessionId = sessionId;
    stateModule.saveCurrentSession();

    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 1);
    assert.equal(history[0].id, sessionId);
    assert.equal(history[0].messages.length, 3);
    assert.equal(stateModule.state.currentSessionId, sessionId);
});

// ─── Storage quota & sanitization ──────────────────────────────────────────────

// localStorage mock that enforces a total size limit, throwing the same
// QuotaExceededError DOMException a real browser throws.
function makeQuotaLocalStorage(limit) {
    const store = new Map();
    let size = 0;
    return {
        getItem(k) { return store.has(k) ? store.get(k) : null; },
        setItem(k, v) {
            v = String(v);
            const old = store.has(k) ? store.get(k).length : 0;
            if (size - old + v.length > limit) {
                throw new DOMException("The quota exceeded", "QuotaExceededError");
            }
            size += v.length - old;
            store.set(k, v);
        },
        removeItem(k) {
            if (store.has(k)) { size -= store.get(k).length; store.delete(k); }
        },
        clear() { store.clear(); size = 0; },
    };
}

await runTest("saveCurrentSession strips image data URLs from persisted history", () => {
    globalThis.localStorage.clear();
    stateModule.state.sessionHistory = [];
    const bigB64 = "A".repeat(20000);
    stateModule.state.chatMessages = [
        { role: "user", content: [
            { type: "text", text: "Look at this" },
            { type: "image_url", image_url: { url: `data:image/png;base64,${bigB64}` } },
        ] },
        { role: "assistant", content: "Nice image!" },
    ];
    stateModule.state.selectedModel = "vision-model";
    stateModule.state.currentSessionId = null;

    stateModule.saveCurrentSession();

    const storedRaw = globalThis.localStorage.getItem("lm_console_history");
    const stored = JSON.parse(storedRaw);
    assert.ok(!storedRaw.includes("data:image"), "persisted history must not contain base64 image data");
    assert.equal(stored.length, 1);
    const parts = stored[0].messages[0].content;
    assert.equal(parts[0].text, "Look at this");
    assert.equal(parts[1].type, "text");
    assert.match(parts[1].text, /\[image attached: image\/png\]/);
    // The live in-memory session keeps the full payload for the active
    // conversation (multimodal context is preserved for the current chat).
    assert.equal(
        stateModule.state.chatMessages[0].content[1].image_url.url,
        `data:image/png;base64,${bigB64}`
    );
});

await runTest("saveCurrentSession strips audio and file data URLs from persisted history", () => {
    globalThis.localStorage.clear();
    stateModule.state.sessionHistory = [];
    const bigAudio = "A".repeat(20000);
    const bigPdf = "B".repeat(20000);
    stateModule.state.chatMessages = [
        { role: "user", content: [
            { type: "text", text: "Listen and read" },
            { type: "input_audio", file_data: `data:audio/wav;base64,${bigAudio}` },
            { type: "input_file", file_data: `data:application/pdf;base64,${bigPdf}` },
        ] },
        { role: "assistant", content: "Got both." },
    ];
    stateModule.state.selectedModel = "multimodal-model";
    stateModule.state.currentSessionId = null;

    stateModule.saveCurrentSession();

    const storedRaw = globalThis.localStorage.getItem("lm_console_history");
    const stored = JSON.parse(storedRaw);
    assert.ok(!storedRaw.includes("data:audio"), "persisted history must not contain base64 audio data");
    assert.ok(!storedRaw.includes("data:application"), "persisted history must not contain base64 file data");
    assert.ok(!storedRaw.includes(bigAudio.slice(0, 1000)), "audio base64 payload must not be persisted");
    assert.ok(!storedRaw.includes(bigPdf.slice(0, 1000)), "file base64 payload must not be persisted");
    assert.equal(stored.length, 1);
    const parts = stored[0].messages[0].content;
    assert.equal(parts[0].text, "Listen and read");
    assert.equal(parts[1].type, "text");
    assert.match(parts[1].text, /\[input_audio attached: audio\/wav\]/);
    assert.equal(parts[2].type, "text");
    assert.match(parts[2].text, /\[input_file attached: application\/pdf\]/);
    // The live in-memory session keeps the full payloads.
    assert.equal(
        stateModule.state.chatMessages[0].content[1].file_data,
        `data:audio/wav;base64,${bigAudio}`
    );
    assert.equal(
        stateModule.state.chatMessages[0].content[2].file_data,
        `data:application/pdf;base64,${bigPdf}`
    );
});

await runTest("history persistence drops oldest sessions when quota exceeded", () => {
    const original = globalThis.localStorage;
    // ~540 chars per session: 3 sessions (~1620) exceed the limit, 2 fit.
    globalThis.localStorage = makeQuotaLocalStorage(1300);
    const warnings = [];
    globalThis.window.addEventListener("lmconsole:storage-warning", (e) => warnings.push(e.detail));

    try {
        const mk = (id) => ({
            id,
            createdAt: new Date().toISOString(),
            model: "m",
            messages: [{ role: "user", content: "x".repeat(400) }],
            preview: id,
        });
        stateModule.state.sessionHistory = [mk("newest"), mk("middle"), mk("oldest")];

        stateModule.saveSessionHistory(); // must not throw

        const stored = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
        assert.equal(stored.length, 2, "oldest session should have been dropped");
        assert.equal(stored[0].id, "newest", "newest session must survive");
        assert.equal(stateModule.state.sessionHistory.length, 2, "in-memory history syncs with persisted");
        assert.ok(warnings.some(w => w.historyChanged), "user should be warned about trimming");
    } finally {
        globalThis.localStorage = original;
    }
});

await runTest("loadSessionHistory sanitizes legacy image data and re-persists", () => {
    globalThis.localStorage.clear();
    const legacy = [{
        id: "legacy-1",
        createdAt: new Date().toISOString(),
        model: "vision-model",
        messages: [
            { role: "user", content: [
                { type: "text", text: "img" },
                { type: "image_url", image_url: { url: `data:image/jpeg;base64,${"Z".repeat(5000)}` } },
            ] },
        ],
        preview: "img",
    }];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(legacy));

    stateModule.loadSessionHistory();

    const parts = stateModule.state.sessionHistory[0].messages[0].content;
    assert.equal(parts[1].type, "text", "legacy data URL replaced with placeholder");
    const storedRaw = globalThis.localStorage.getItem("lm_console_history");
    assert.ok(!storedRaw.includes("data:image"), "stored value re-persisted without base64 data");
    assert.ok(storedRaw.length < JSON.stringify(legacy).length, "re-persisted value is smaller");
});

// ─── Theme tests ───────────────────────────────────────────────

console.log("\nTheme module:");

await runTest("state.theme defaults to cyberpunk", () => {
    assert.equal(stateModule.state.theme, "cyberpunk");
});

await runTest("applyTheme sets state.theme and updates stylesheet href", () => {
    _resetDomCache();
    globalThis.localStorage.clear();
    globalThis._dispatchedEvents = [];
    globalThis._listenerMap = {};

    stateModule.state.theme = "cyberpunk";
    // Set initial href
    _domCache["theme-stylesheet"] = _createMockElement();
    _domCache["theme-stylesheet"].id = "theme-stylesheet";
    _domCache["theme-stylesheet"].href = "/static/css/theme-cyberpunk.css";

    stateModule.applyTheme("light");

    assert.equal(stateModule.state.theme, "light");
    assert.equal(_domCache["theme-stylesheet"].href, "/static/css/theme-light.css");
});

await runTest("applyTheme calls saveSettings to persist theme", () => {
    _resetDomCache();
    globalThis.localStorage.clear();
    globalThis._dispatchedEvents = [];
    globalThis._listenerMap = {};

    stateModule.state.theme = "cyberpunk";
    _domCache["theme-stylesheet"] = _createMockElement();
    _domCache["theme-stylesheet"].id = "theme-stylesheet";
    _domCache["theme-stylesheet"].href = "/static/css/theme-cyberpunk.css";

    stateModule.applyTheme("warm");

    const saved = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.equal(saved.theme, "warm");
});

await runTest("applyTheme dispatches themechanged event", () => {
    _resetDomCache();
    globalThis.localStorage.clear();
    globalThis._dispatchedEvents = [];
    globalThis._listenerMap = {};

    let eventReceived = false;
    let eventDetail = null;
    globalThis.window.addEventListener("themechanged", (e) => {
        eventReceived = true;
        eventDetail = e.detail;
    });

    stateModule.state.theme = "cyberpunk";
    _domCache["theme-stylesheet"] = _createMockElement();
    _domCache["theme-stylesheet"].id = "theme-stylesheet";
    _domCache["theme-stylesheet"].href = "/static/css/theme-cyberpunk.css";

    stateModule.applyTheme("light");

    assert.equal(eventReceived, true, "themechanged event should be dispatched");
    assert.equal(eventDetail.theme, "light");
});

await runTest("applyTheme reinitializes mermaid with matching theme", () => {
    _resetDomCache();
    globalThis.localStorage.clear();
    globalThis._dispatchedEvents = [];
    globalThis._listenerMap = {};

    stateModule.state.theme = "cyberpunk";
    _domCache["theme-stylesheet"] = _createMockElement();
    _domCache["theme-stylesheet"].id = "theme-stylesheet";
    _domCache["theme-stylesheet"].href = "/static/css/theme-cyberpunk.css";

    // Track mermaid.initialize calls
    let lastMermaidTheme = null;
    const origMermaid = globalThis.mermaid;
    globalThis.mermaid = {
        initialize: (opts) => { lastMermaidTheme = opts.theme; },
        render: async () => ({ svg: "<svg></svg>" }),
    };

    try {
        stateModule.applyTheme("light");
        assert.equal(lastMermaidTheme, "default", "Light theme should map to mermaid 'default'");

        stateModule.applyTheme("cyberpunk");
        assert.equal(lastMermaidTheme, "dark", "Cyberpunk theme should map to mermaid 'dark'");

        stateModule.applyTheme("warm");
        assert.equal(lastMermaidTheme, "neutral", "Warm theme should map to mermaid 'neutral'");
    } finally {
        globalThis.mermaid = origMermaid;
    }
});

await runTest("saveSettings includes theme", () => {
    globalThis.localStorage.clear();
    stateModule.state.theme = "warm";
    stateModule.saveSettings();

    const saved = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.equal(saved.theme, "warm");
});

await runTest("loadSettings restores theme from localStorage", () => {
    globalThis.localStorage.clear();
    globalThis.localStorage.setItem("lm_console_settings", JSON.stringify({
        endpoint: "http://localhost:1234",
        apiToken: "",
        systemPrompt: "You are a helpful assistant.",
        temperature: 0.7,
        selectedModel: null,
        toolCallEnabled: false,
        theme: "light",
    }));

    stateModule.state.theme = "cyberpunk";
    stateModule.state.systemPrompt = "";
    stateModule.state.temperature = null;

    const dom = {
        endpoint: { value: "" },
        apiToken: { value: "" },
        systemPrompt: { value: "", disabled: true },
        systemPromptUnset: { checked: true },
        temperature: { value: "", disabled: true },
        temperatureUnset: { checked: true },
        temperatureValue: { textContent: "—" },
        toolCallToggle: { checked: false },
    };
    stateModule.loadSettings(dom);

    assert.equal(stateModule.state.theme, "light");
    // Legacy stored values are kept as user data (no migration): the old
    // default prompt / 0.7 restore as custom (enabled) settings.
    assert.equal(stateModule.state.systemPrompt, "You are a helpful assistant.");
    assert.equal(dom.systemPrompt.value, "You are a helpful assistant.");
    assert.equal(dom.systemPromptUnset.checked, false);
    assert.equal(dom.systemPrompt.disabled, false);
    assert.equal(stateModule.state.temperature, 0.7);
    assert.equal(dom.temperature.value, 0.7);
    assert.equal(dom.temperatureValue.textContent, "0.70");
    assert.equal(dom.temperatureUnset.checked, false);
    assert.equal(dom.temperature.disabled, false);
});

// ─── Abort/cancellation tests (abortActiveRequest) ─────────────

await runTest("abortActiveRequest calls abort() on stored controller and nulls it", () => {
    stateModule.state.abortController = null;
    const controller = new globalThis.AbortController();
    let abortCalled = false;
    controller.signal.addEventListener("abort", () => { abortCalled = true; });
    stateModule.state.abortController = controller;
    stateModule.abortActiveRequest();
    assert.equal(controller.signal.aborted, true, "controller.abort() should have been called");
    assert.equal(abortCalled, true, "signal 'abort' event should have fired");
    assert.equal(stateModule.state.abortController, null, "abortController should be nulled out after abort");
});

await runTest("abortActiveRequest is a no-op when abortController is null", () => {
    stateModule.state.abortController = null;
    assert.doesNotThrow(() => stateModule.abortActiveRequest());
    assert.equal(stateModule.state.abortController, null, "abortController should remain null");
});

// ─── UI utility tests ──────────────────────────────────────────

console.log("\nUI module:");

await runTest("formatBytes converts correctly", () => {
    assert.equal(uiModule.formatBytes(500), "500 B");
    assert.equal(uiModule.formatBytes(1536), "1.5 KB");
    assert.equal(uiModule.formatBytes(1572864), "1.5 MB");
    assert.equal(uiModule.formatBytes(1610612736), "1.5 GB");
});

await runTest("escapeHtml sanitizes input", () => {
    assert.equal(uiModule.escapeHtml("<script>alert('xss')</script>"),
                 "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;");
    assert.equal(uiModule.escapeHtml("A & B"), "A &amp; B");
    // Attribute-breakout payload: no raw quote characters may survive, and
    // < > must be escaped, so output is safe inside HTML attributes too.
    const breakout = uiModule.escapeHtml('"><img src=x onerror=alert(1)>');
    assert.ok(!breakout.includes('"'), "no raw double quotes may survive");
    assert.ok(!breakout.includes("'"), "no raw single quotes may survive");
    assert.ok(!breakout.includes("<"), "< must be escaped");
    assert.ok(!breakout.includes(">"), "> must be escaped");
});

await runTest("updateMetrics formats values", () => {
    const dom = {
        metricTpsValue: { textContent: "" },
        metricTtftValue: { textContent: "" },
        metricTokensValue: { textContent: "" },
    };

    uiModule.updateMetrics(dom, { tokensPerSecond: 45.67, timeToFirstToken: 1.234, totalTokens: 150 });

    assert.equal(dom.metricTpsValue.textContent, "45.7");
    assert.equal(dom.metricTtftValue.textContent, "1.23s");
    assert.equal(dom.metricTokensValue.textContent, "150");
});

await runTest("updateMetrics uses state when no metrics passed", () => {
    stateModule.state.metrics = { tokensPerSecond: 10, timeToFirstToken: 0.5, totalTokens: 100 };
    const dom = {
        metricTpsValue: { textContent: "" },
        metricTtftValue: { textContent: "" },
        metricTokensValue: { textContent: "" },
    };

    uiModule.updateMetrics(dom);

    assert.equal(dom.metricTpsValue.textContent, "10.0");
    assert.equal(dom.metricTtftValue.textContent, "0.50s");
    assert.equal(dom.metricTokensValue.textContent, "100");
});

// ─── Session lifecycle tests (continueSession, deleteSession) ──

console.log("\nSession lifecycle:");

// Mock DOM for history.js operations
function _createHistoryDom() {
    return {
        historyList: {
            innerHTML: "",
            querySelectorAll() { return []; },
            addEventListener() {},
        },
        chatMessages: {
            innerHTML: "",
            style: { display: "flex" },
            querySelector() { return null; },
        },
        chatInput: { disabled: false },
        sendBtn: { disabled: false },
        emptyState: { style: { display: "none" } },
        chatHeader: { style: { display: "flex" } },
        chatMetrics: { style: { display: "flex" } },
        chatModelLabel: { textContent: "" },
    };
}

await runTest("continueSession restores messages and sets currentSessionId", () => {
    globalThis.localStorage.clear();
    const sessionId = "test-session-continue";
    const sessionMessages = [
        { role: "user", content: "Hello" },
        { role: "assistant", content: "Hi there!" },
        { role: "user", content: "How are you?" },
    ];

    stateModule.state.sessionHistory = [{
        id: sessionId,
        createdAt: new Date().toISOString(),
        model: "llama-3.1-8b",
        messages: sessionMessages,
        preview: "Hello",
    }];
    stateModule.state.models = [{ key: "llama-3.1-8b", display_name: "Llama 3.1 8B" }];
    stateModule.state.chatMessages = [{ role: "user", content: "different chat" }];
    stateModule.state.currentSessionId = null;
    stateModule.state.selectedModel = null;

    const dom = _createHistoryDom();
    historyModule.continueSession(dom, sessionId);

    assert.equal(stateModule.state.currentSessionId, sessionId);
    assert.equal(stateModule.state.chatMessages.length, 3);
    assert.equal(stateModule.state.chatMessages[0].content, "Hello");
    assert.equal(stateModule.state.chatMessages[1].content, "Hi there!");
    assert.equal(stateModule.state.chatMessages[2].content, "How are you?");
    assert.equal(stateModule.state.selectedModel, "llama-3.1-8b");
    // Metrics reset
    assert.equal(stateModule.state.metrics.tokensPerSecond, 0);
    assert.equal(stateModule.state.metrics.timeToFirstToken, null);
    assert.equal(stateModule.state.metrics.totalTokens, 0);
});

await runTest("continueSession saves current chat before switching", () => {
    globalThis.localStorage.clear();

    // Pre-existing session in history
    stateModule.state.sessionHistory = [{
        id: "old-session",
        createdAt: new Date().toISOString(),
        model: "model-a",
        messages: [{ role: "user", content: "old" }],
        preview: "old",
    }];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(stateModule.state.sessionHistory));

    // Current active chat
    const targetSessionId = "target-session";
    stateModule.state.sessionHistory.push({
        id: targetSessionId,
        createdAt: new Date().toISOString(),
        model: "model-b",
        messages: [{ role: "user", content: "target msg" }],
        preview: "target msg",
    });
    stateModule.state.chatMessages = [
        { role: "user", content: "current chat msg" },
        { role: "assistant", content: "current reply" },
    ];
    stateModule.state.selectedModel = "model-c";
    stateModule.state.currentSessionId = "current-id";

    const dom = _createHistoryDom();
    historyModule.continueSession(dom, targetSessionId);

    // Current chat should have been saved
    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    // Should have: old-session + saved current chat + target-session = 3
    assert.equal(history.length, 3);

    // Current chat is now restored to target session
    assert.equal(stateModule.state.currentSessionId, targetSessionId);
    assert.equal(stateModule.state.chatMessages[0].content, "target msg");
});

await runTest("continueSession shows error for unknown session", () => {
    globalThis.localStorage.clear();
    stateModule.state.sessionHistory = [];
    stateModule.state.chatMessages = [{ role: "user", content: "existing" }];

    const dom = _createHistoryDom();
    historyModule.continueSession(dom, "nonexistent-id");

    // Session not found — chat should remain unchanged
    assert.equal(stateModule.state.chatMessages.length, 1);
    assert.equal(stateModule.state.chatMessages[0].content, "existing");
});

await runTest("deleteSession removes from history", () => {
    globalThis.localStorage.clear();
    const deleteId = "session-to-delete";
    const keepId = "session-to-keep";

    stateModule.state.sessionHistory = [
        {
            id: deleteId,
            createdAt: new Date().toISOString(),
            model: "model-a",
            messages: [{ role: "user", content: "delete me" }],
            preview: "delete me",
        },
        {
            id: keepId,
            createdAt: new Date().toISOString(),
            model: "model-b",
            messages: [{ role: "user", content: "keep me" }],
            preview: "keep me",
        },
    ];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(stateModule.state.sessionHistory));
    stateModule.state.currentSessionId = null;

    const dom = _createHistoryDom();
    historyModule.deleteSession(dom, deleteId);

    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 1);
    assert.equal(history[0].id, keepId);
});

await runTest("deleteSession clears current chat if it matches", () => {
    globalThis.localStorage.clear();
    const currentId = "current-session";

    stateModule.state.sessionHistory = [{
        id: currentId,
        createdAt: new Date().toISOString(),
        model: "model-a",
        messages: [{ role: "user", content: "current msg" }],
        preview: "current msg",
    }];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(stateModule.state.sessionHistory));
    stateModule.state.chatMessages = [
        { role: "user", content: "current msg" },
        { role: "assistant", content: "reply" },
    ];
    stateModule.state.currentSessionId = currentId;

    const dom = _createHistoryDom();
    historyModule.deleteSession(dom, currentId);

    // Chat should be cleared
    assert.equal(stateModule.state.chatMessages.length, 0);
    assert.equal(stateModule.state.currentSessionId, null);
    // History should be empty
    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 0);
    // DOM elements updated
    assert.equal(dom.emptyState.style.display, "flex");
    assert.equal(dom.chatHeader.style.display, "none");
    assert.equal(dom.chatMessages.style.display, "none");
    assert.equal(dom.chatMetrics.style.display, "none");
    assert.equal(dom.chatInput.disabled, true);
    assert.equal(dom.sendBtn.disabled, true);
});

await runTest("deleteSession leaves current chat if deleting different session", () => {
    globalThis.localStorage.clear();
    const deleteId = "other-session";
    const currentId = "current-session";

    stateModule.state.sessionHistory = [
        {
            id: deleteId,
            createdAt: new Date().toISOString(),
            model: "model-a",
            messages: [{ role: "user", content: "delete me" }],
            preview: "delete me",
        },
        {
            id: currentId,
            createdAt: new Date().toISOString(),
            model: "model-b",
            messages: [{ role: "user", content: "keep me" }],
            preview: "keep me",
        },
    ];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(stateModule.state.sessionHistory));
    stateModule.state.chatMessages = [{ role: "user", content: "keep me" }];
    stateModule.state.currentSessionId = currentId;

    const dom = _createHistoryDom();
    historyModule.deleteSession(dom, deleteId);

    // Current chat should remain
    assert.equal(stateModule.state.chatMessages.length, 1);
    assert.equal(stateModule.state.currentSessionId, currentId);
    // History has only the kept session
    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 1);
    assert.equal(history[0].id, currentId);
});

await runTest("deleteSession shows error for unknown session", () => {
    globalThis.localStorage.clear();
    stateModule.state.sessionHistory = [{
        id: "existing",
        createdAt: new Date().toISOString(),
        model: "model-a",
        messages: [{ role: "user", content: "msg" }],
        preview: "msg",
    }];
    globalThis.localStorage.setItem("lm_console_history", JSON.stringify(stateModule.state.sessionHistory));
    stateModule.state.chatMessages = [{ role: "user", content: "current" }];
    stateModule.state.currentSessionId = "current-id";

    const dom = _createHistoryDom();
    historyModule.deleteSession(dom, "nonexistent-id");

    // Nothing should change
    assert.equal(stateModule.state.chatMessages.length, 1);
    assert.equal(stateModule.state.currentSessionId, "current-id");
    const history = JSON.parse(globalThis.localStorage.getItem("lm_console_history"));
    assert.equal(history.length, 1);
});

// ─── Profiles tests (renderProfileList, saveProfile, loadProfile,
// ─── deleteProfile) ─────────────────────────────────────────────

console.log("\nProfiles:");

// Mock DOM for profiles.js operations
function _createProfileDom() {
    return {
        profileName: { value: "" },
        profileList: {
            innerHTML: "",
            querySelectorAll() { return []; },
            addEventListener() {},
        },
        endpoint: { value: "" },
        apiToken: { value: "" },
        systemPrompt: { value: "", disabled: false },
        systemPromptUnset: { checked: false },
        temperature: { value: "", disabled: false },
        temperatureUnset: { checked: false },
        temperatureValue: { textContent: "" },
        toolCallToggle: { checked: false },
    };
}

await runTest("state.profiles defaults to []", () => {
    assert.deepEqual(stateModule.state.profiles, [], "fresh state has no profiles");
});

await runTest("loadProfiles filters malformed stored entries", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [{ name: "stale" }];
    globalThis.localStorage.setItem("lm_console_profiles", JSON.stringify([
        { name: "good", endpoint: "http://good:1234" },
        { name: "   " },           // whitespace-only name
        { name: 42 },              // non-string name
        "not-an-object",
        null,
        42,
    ]));

    stateModule.loadProfiles();

    assert.equal(stateModule.state.profiles.length, 1, "only the valid entry survives");
    assert.equal(stateModule.state.profiles[0].name, "good");
    assert.equal(stateModule.state.profiles[0].endpoint, "http://good:1234");
});

await runTest("loadProfiles resets to [] on corrupt or non-array JSON", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [{ name: "stale" }];
    globalThis.localStorage.setItem("lm_console_profiles", "{not valid json");
    stateModule.loadProfiles();
    assert.deepEqual(stateModule.state.profiles, [], "corrupt JSON yields empty list");

    globalThis.localStorage.setItem("lm_console_profiles", JSON.stringify({ name: "not-an-array" }));
    stateModule.loadProfiles();
    assert.deepEqual(stateModule.state.profiles, [], "non-array JSON yields empty list");
});

await runTest("saveProfile creates a profile and persists it", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [];
    stateModule.state.endpoint = "http://profile-endpoint:1234";
    stateModule.state.apiToken = "tok-123";
    stateModule.state.selectedModel = "model-p";
    stateModule.state.systemPrompt = "Profile prompt";
    stateModule.state.temperature = 1.1;
    stateModule.state.toolCallEnabled = true;

    const dom = _createProfileDom();
    dom.profileName.value = "  work-profile  "; // trimmed on save
    profilesModule.saveProfile(dom);

    assert.equal(stateModule.state.profiles.length, 1);
    const p = stateModule.state.profiles[0];
    assert.equal(p.name, "work-profile");
    assert.equal(p.endpoint, "http://profile-endpoint:1234");
    assert.equal(p.apiToken, "tok-123");
    assert.equal(p.selectedModel, "model-p");
    assert.equal(p.systemPrompt, "Profile prompt");
    assert.equal(p.temperature, 1.1);
    assert.equal(p.toolCallEnabled, true);
    assert.ok(p.savedAt, "savedAt timestamp recorded");

    // Round-trips through localStorage
    const stored = JSON.parse(globalThis.localStorage.getItem("lm_console_profiles"));
    assert.equal(stored.length, 1);
    assert.equal(stored[0].name, "work-profile");
    assert.equal(stored[0].endpoint, "http://profile-endpoint:1234");
});

await runTest("saveProfile with same name modifies in place (still one entry)", () => {
    // "work-profile" exists from the previous test
    assert.equal(stateModule.state.profiles.length, 1);

    stateModule.state.endpoint = "http://changed-endpoint:9999";
    stateModule.state.temperature = 0.25;

    const dom = _createProfileDom();
    dom.profileName.value = "work-profile";
    profilesModule.saveProfile(dom);

    assert.equal(stateModule.state.profiles.length, 1, "same name must not create a second entry");
    assert.equal(stateModule.state.profiles[0].name, "work-profile");
    assert.equal(stateModule.state.profiles[0].endpoint, "http://changed-endpoint:9999", "updated endpoint");
    assert.equal(stateModule.state.profiles[0].temperature, 0.25, "updated temperature");
    assert.equal(stateModule.state.profiles[0].apiToken, "tok-123", "untouched field kept");

    const stored = JSON.parse(globalThis.localStorage.getItem("lm_console_profiles"));
    assert.equal(stored.length, 1, "storage holds one entry after modify");
    assert.equal(stored[0].endpoint, "http://changed-endpoint:9999");
});

await runTest("saveProfile rejects an empty name", () => {
    const before = stateModule.state.profiles.length;
    const dom = _createProfileDom();
    dom.profileName.value = "   ";
    profilesModule.saveProfile(dom);
    assert.equal(stateModule.state.profiles.length, before, "no profile added for blank name");
});

await runTest("deleteProfile removes the profile and persists", () => {
    const dom = _createProfileDom();
    profilesModule.deleteProfile(dom, "work-profile");

    assert.equal(stateModule.state.profiles.length, 0);
    const stored = JSON.parse(globalThis.localStorage.getItem("lm_console_profiles"));
    assert.equal(stored.length, 0, "storage empty after delete");

    // Deleting an unknown profile is a no-op
    profilesModule.deleteProfile(dom, "no-such-profile");
    assert.equal(stateModule.state.profiles.length, 0);
});

await runTest("loadProfile applies set values to state and DOM controls", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [{
        name: "load-me",
        endpoint: "http://load-endpoint:4321",
        apiToken: "tok-456",
        selectedModel: "model-l",
        systemPrompt: "Load prompt",
        temperature: 1.25,
        toolCallEnabled: true,
        savedAt: new Date().toISOString(),
    }];
    // Reset state to different values to prove the profile overrides them
    stateModule.state.endpoint = "http://localhost:1234";
    stateModule.state.apiToken = "";
    stateModule.state.selectedModel = null;
    stateModule.state.systemPrompt = "";
    stateModule.state.temperature = null;
    stateModule.state.toolCallEnabled = false;
    stateModule.state.connected = false;

    const dom = _createProfileDom();
    profilesModule.loadProfile(dom, "load-me");

    // State
    assert.equal(stateModule.state.endpoint, "http://load-endpoint:4321");
    assert.equal(stateModule.state.apiToken, "tok-456");
    assert.equal(stateModule.state.selectedModel, "model-l");
    assert.equal(stateModule.state.systemPrompt, "Load prompt");
    assert.equal(stateModule.state.temperature, 1.25);
    assert.equal(stateModule.state.toolCallEnabled, true);
    // DOM controls — set values: unset checkboxes off, controls enabled
    assert.equal(dom.endpoint.value, "http://load-endpoint:4321");
    assert.equal(dom.apiToken.value, "tok-456");
    assert.equal(dom.systemPrompt.value, "Load prompt");
    assert.equal(dom.systemPromptUnset.checked, false);
    assert.equal(dom.systemPrompt.disabled, false);
    assert.equal(dom.temperature.value, 1.25);
    assert.equal(dom.temperatureValue.textContent, "1.25");
    assert.equal(dom.temperatureUnset.checked, false);
    assert.equal(dom.temperature.disabled, false);
    assert.equal(dom.toolCallToggle.checked, true);
    // Settings persisted
    const saved = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.equal(saved.systemPrompt, "Load prompt");
    assert.equal(saved.temperature, 1.25);
    assert.equal(saved.endpoint, "http://load-endpoint:4321");
    stateModule.state.connected = false;
});

await runTest("loadProfile with unset prompt/temperature checks the server-default toggles", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [{
        name: "unset-profile",
        endpoint: "http://unset:1234",
        apiToken: "",
        selectedModel: null,
        systemPrompt: "",
        temperature: null,
        toolCallEnabled: false,
        savedAt: new Date().toISOString(),
    }];

    const dom = _createProfileDom();
    profilesModule.loadProfile(dom, "unset-profile");

    assert.equal(stateModule.state.systemPrompt, "");
    assert.equal(stateModule.state.temperature, null);
    assert.equal(dom.systemPrompt.value, "");
    assert.equal(dom.systemPromptUnset.checked, true, "prompt unset checkbox checked");
    assert.equal(dom.systemPrompt.disabled, true, "prompt textarea disabled");
    assert.equal(dom.temperatureUnset.checked, true, "temperature unset checkbox checked");
    assert.equal(dom.temperature.disabled, true, "temperature slider disabled");
    assert.equal(dom.temperatureValue.textContent, "—", "value span shows em dash");
    assert.equal(dom.toolCallToggle.checked, false);
});

await runTest("loadProfile refreshes model list + chat controls when connected + endpoint unchanged", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [{
        name: "conn-profile",
        endpoint: "http://conn:1234",
        apiToken: "",
        selectedModel: null,
        systemPrompt: "",
        temperature: null,
        toolCallEnabled: false,
        savedAt: new Date().toISOString(),
    }];

    const dom = _createProfileDom();

    // Connected to the SAME endpoint/apiToken as the profile: both
    // refreshes must run and no disconnect
    globalThis._profileDisconnectCalls = 0;
    globalThis._profileModelListCalls = 0;
    globalThis._profileEnableChatCalls = 0;
    stateModule.state.connected = true;
    stateModule.state.endpoint = "http://conn:1234";
    stateModule.state.apiToken = "";
    profilesModule.loadProfile(dom, "conn-profile");
    assert.equal(globalThis._profileDisconnectCalls, 0, "no disconnect when endpoint unchanged");
    assert.equal(globalThis._profileModelListCalls, 1, "renderModelList called when connected");
    assert.equal(globalThis._profileEnableChatCalls, 1, "enableChatControls called when connected");

    // Disconnected: loading a profile must not touch model/chat UI or the connection
    globalThis._profileDisconnectCalls = 0;
    globalThis._profileModelListCalls = 0;
    globalThis._profileEnableChatCalls = 0;
    stateModule.state.connected = false;
    stateModule.state.endpoint = "http://conn:1234";
    stateModule.state.apiToken = "";
    profilesModule.loadProfile(dom, "conn-profile");
    assert.equal(globalThis._profileDisconnectCalls, 0, "no disconnect when already disconnected");
    assert.equal(globalThis._profileModelListCalls, 0, "renderModelList not called when disconnected");
    assert.equal(globalThis._profileEnableChatCalls, 0, "enableChatControls not called when disconnected");
    stateModule.state.connected = false;
});

await runTest("loadProfile disconnects when connected and endpoint or apiToken changed", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [{
        name: "moved-profile",
        endpoint: "http://moved:4321",
        apiToken: "tok-moved",
        selectedModel: "model-moved",
        systemPrompt: "",
        temperature: null,
        toolCallEnabled: false,
        savedAt: new Date().toISOString(),
    }];

    const dom = _createProfileDom();

    // Connected to a DIFFERENT endpoint: load must disconnect (the live
    // connection is bound to the old endpoint) and prompt a reconnect —
    // not silently refresh the old endpoint's model list.
    globalThis._profileDisconnectCalls = 0;
    globalThis._profileModelListCalls = 0;
    globalThis._profileEnableChatCalls = 0;
    globalThis._profileToasts = [];
    stateModule.state.connected = true;
    stateModule.state.endpoint = "http://old-host:1234";
    stateModule.state.apiToken = "tok-old";
    profilesModule.loadProfile(dom, "moved-profile");

    assert.equal(globalThis._profileDisconnectCalls, 1, "disconnect called when endpoint changed while connected");
    assert.equal(globalThis._profileModelListCalls, 0, "model list not refreshed after disconnect");
    assert.equal(globalThis._profileEnableChatCalls, 0, "chat controls not refreshed after disconnect");
    assert.ok(
        globalThis._profileToasts.some(t => t.message === "Reconnect to apply endpoint changes" && t.type === "info"),
        "info toast prompts the user to reconnect",
    );
    // The profile fields themselves are still applied to state
    assert.equal(stateModule.state.endpoint, "http://moved:4321");
    assert.equal(stateModule.state.apiToken, "tok-moved");
    // disconnect() resets state.selectedModel — the profile's model must be
    // re-applied after the teardown so the selection survives to reconnect
    assert.equal(
        stateModule.state.selectedModel, "model-moved",
        "the profile's model selection must survive the disconnect() teardown",
    );
    stateModule.state.connected = false;

    // An apiToken-only change (same endpoint) must also disconnect
    globalThis._profileDisconnectCalls = 0;
    globalThis._profileModelListCalls = 0;
    globalThis._profileEnableChatCalls = 0;
    globalThis._profileToasts = [];
    stateModule.state.connected = true;
    stateModule.state.endpoint = "http://moved:4321"; // same as the profile
    stateModule.state.apiToken = "tok-stale";         // differs from the profile
    profilesModule.loadProfile(dom, "moved-profile");

    assert.equal(globalThis._profileDisconnectCalls, 1, "disconnect called when only apiToken changed");
    assert.equal(globalThis._profileModelListCalls, 0, "model list not refreshed after disconnect (token change)");
    assert.equal(globalThis._profileEnableChatCalls, 0, "chat controls not refreshed after disconnect (token change)");
    assert.equal(
        stateModule.state.selectedModel, "model-moved",
        "the profile's model selection must survive the token-change disconnect too",
    );
    stateModule.state.connected = false;
});

await runTest("loadProfile nulls a stale model when connected and endpoint unchanged", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [{
        name: "stale-model-profile",
        endpoint: "http://stale:1234",
        apiToken: "",
        selectedModel: "model-gone",
        systemPrompt: "",
        temperature: null,
        toolCallEnabled: false,
        savedAt: new Date().toISOString(),
    }];

    const dom = _createProfileDom();

    globalThis._profileDisconnectCalls = 0;
    globalThis._profileModelListCalls = 0;
    globalThis._profileEnableChatCalls = 0;
    stateModule.state.connected = true;
    stateModule.state.endpoint = "http://stale:1234"; // same endpoint
    stateModule.state.apiToken = "";
    // Live list no longer contains the profile's model (unloaded/renamed)
    stateModule.state.models = [{ key: "model-present", display_name: "Present" }];
    stateModule.state.selectedModel = "model-present";

    profilesModule.loadProfile(dom, "stale-model-profile");

    assert.equal(globalThis._profileDisconnectCalls, 0, "no disconnect when endpoint unchanged");
    assert.equal(
        stateModule.state.selectedModel, null,
        "a profile model missing from the live list must be nulled",
    );
    assert.equal(globalThis._profileModelListCalls, 1, "model list re-rendered after the nulling");
    assert.equal(globalThis._profileEnableChatCalls, 1, "chat controls re-evaluated with the nulled selection");
    const saved = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.equal(saved.selectedModel, null, "nulled selection persisted");

    // A model that IS in the live list stays selected (no over-nulling)
    stateModule.state.models = [{ key: "model-present" }, { key: "model-kept" }];
    stateModule.state.selectedModel = "model-kept";
    stateModule.state.profiles = [{
        name: "stale-model-profile",
        endpoint: "http://stale:1234",
        apiToken: "",
        selectedModel: "model-kept",
        systemPrompt: "",
        temperature: null,
        toolCallEnabled: false,
        savedAt: new Date().toISOString(),
    }];
    globalThis._profileModelListCalls = 0;
    globalThis._profileEnableChatCalls = 0;
    profilesModule.loadProfile(dom, "stale-model-profile");
    assert.equal(
        stateModule.state.selectedModel, "model-kept",
        "a model present in the live list must stay selected",
    );
    stateModule.state.connected = false;
    stateModule.state.models = [];
});

await runTest("renderProfileList renders empty state and items without throwing", () => {
    globalThis.localStorage.clear();
    stateModule.state.profiles = [];
    let dom = _createProfileDom();
    profilesModule.renderProfileList(dom);
    assert.ok(dom.profileList.innerHTML.includes("empty-state"), "empty state shown when no profiles");

    stateModule.state.profiles = [{
        name: "render-me",
        endpoint: "http://render:1234",
        apiToken: "",
        selectedModel: "model-r",
        systemPrompt: "",
        temperature: null,
        toolCallEnabled: false,
        savedAt: new Date().toISOString(),
    }];
    dom = _createProfileDom();
    profilesModule.renderProfileList(dom);
    assert.ok(dom.profileList.innerHTML.includes("profile-item"), "profile item rendered");
    assert.ok(dom.profileList.innerHTML.includes("load-btn"), "load button rendered");
    assert.ok(dom.profileList.innerHTML.includes("delete-btn"), "delete button rendered");
});

// ─── Summary ────────────────────────────────────────────────────

console.log(`\n${testsPassed} passed, ${testsFailed} failed`);
process.exit(testsFailed > 0 ? 1 : 0);
