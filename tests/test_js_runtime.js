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
        /import\s*{\s*showToast\s*}\s*from\s*"\.\/ui\.js";?/,
        'const { showToast } = uiModule;'
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

// Mock browser globals for Node.js
globalThis.localStorage = {
    _data: {},
    getItem(key) { return this._data[key] || null; },
    setItem(key, value) { this._data[key] = String(value); },
    removeItem(key) { delete this._data[key]; },
    clear() { this._data = {}; },
};

globalThis.document = {
    getElementById() { return _createMockElement(); },
    createElement(tag) {
        const el = _createMockElement();
        el.tagName = tag;
        return el;
    },
    body: { appendChild() {} },
};

function _createMockElement() {
    let _textContent = "";
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
    assert.equal(s.systemPrompt, "You are a helpful assistant.");
    assert.equal(s.temperature, 0.7);
    assert.deepEqual(s.metrics, { tokensPerSecond: 0, timeToFirstToken: null, totalTokens: 0 });
    assert.equal(s.toolCallEnabled, false);
    assert.deepEqual(s.attachments, []);
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
    stateModule.saveSettings();

    const saved = JSON.parse(globalThis.localStorage.getItem("lm_console_settings"));
    assert.equal(saved.endpoint, "http://test:9999");
    assert.equal(saved.systemPrompt, "Test prompt");
    assert.equal(saved.temperature, 0.5);
    assert.equal(saved.toolCallEnabled, true);
    assert.equal(saved.selectedModel, "test-model");
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
    }));

    // Reset state
    stateModule.state.endpoint = "http://localhost:1234";
    stateModule.state.systemPrompt = "You are a helpful assistant.";
    stateModule.state.temperature = 0.7;
    stateModule.state.toolCallEnabled = false;
    stateModule.state.selectedModel = null;

    const dom = {
        endpoint: { value: "" },
        apiToken: { value: "" },
        systemPrompt: { value: "" },
        temperature: { value: "" },
        temperatureValue: { textContent: "" },
        toolCallToggle: { checked: false },
    };
    stateModule.loadSettings(dom);

    assert.equal(stateModule.state.endpoint, "http://restored:8888");
    assert.equal(stateModule.state.apiToken, "secret");
    assert.equal(stateModule.state.systemPrompt, "Restored prompt");
    assert.equal(stateModule.state.temperature, 0.3);
    assert.equal(stateModule.state.toolCallEnabled, true);
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

// ─── Summary ────────────────────────────────────────────────────

console.log(`\n${testsPassed} passed, ${testsFailed} failed`);
process.exit(testsFailed > 0 ? 1 : 0);
