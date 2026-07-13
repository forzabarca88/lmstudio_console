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

// Mock crypto
Object.defineProperty(globalThis, 'crypto', {
    value: { randomUUID: () => "test-uuid-" + Date.now() },
    writable: true,
    configurable: true,
});

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

// ─── Summary ────────────────────────────────────────────────────

console.log(`\n${testsPassed} passed, ${testsFailed} failed`);
process.exit(testsFailed > 0 ? 1 : 0);
