"""Screenshot validation tests using Playwright.

Validates the rendered UI matches expected layout and content (visual tests),
and verifies interactive behavior of UI elements (behavioral tests).

Visual tests: UI element presence, interaction states, and visual rendering.
Behavioral tests: Click interactions, state transitions, toast notifications,
and DOM updates in response to user input.

Tests run against a local server (no external LM Studio required).

Run: uv run python -m unittest tests.test_screenshot -v
"""

import os
import sys
import json
import unittest
import subprocess
import time
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from playwright.sync_api import sync_playwright

# Server configuration
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8090  # Use different port to avoid conflicts
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"

# Screenshot directory
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")

# Click offset for the send button. A positioned .cursor-indicator overlay
# (right:16px within .chat-input-wrapper) sits over the right-center of the
# send button and can intercept pointer events at the button's default center
# click point. Clicking the left portion of the button reliably avoids it.
_SEND_BTN_CLICK_POS = {"x": 10, "y": 20}

# DOM object template for passing to frontend functions via page.evaluate()
DOM_JS = """const dom = {
    endpoint: document.getElementById('endpoint'),
    apiToken: document.getElementById('apiToken'),
    connectBtn: document.getElementById('connectBtn'),
    refreshModelsBtn: document.getElementById('refreshModelsBtn'),
    loadModelBtn: document.getElementById('loadModelBtn'),
    unloadModelBtn: document.getElementById('unloadModelBtn'),
    modelList: document.getElementById('modelList'),
    settingsToggle: document.getElementById('settingsToggle'),
    settingsPanel: document.getElementById('settingsPanel'),
    systemPrompt: document.getElementById('systemPrompt'),
    temperature: document.getElementById('temperature'),
    temperatureValue: document.getElementById('temperatureValue'),
    toolCallToggle: document.getElementById('toolCallToggle'),
    themeSelect: document.getElementById('themeSelect'),
    historyToggle: document.getElementById('historyToggle'),
    historyPanel: document.getElementById('historyPanel'),
    historyList: document.getElementById('historyList'),
    statusDot: document.getElementById('statusDot'),
    statusText: document.getElementById('statusText'),
    emptyState: document.getElementById('emptyState'),
    chatHeader: document.getElementById('chatHeader'),
    chatMetrics: document.getElementById('chatMetrics'),
    metricTpsValue: document.getElementById('metricTpsValue'),
    metricTtftValue: document.getElementById('metricTtftValue'),
    metricTokensValue: document.getElementById('metricTokensValue'),
    chatMessages: document.getElementById('chatMessages'),
    streamingIndicator: document.getElementById('streamingIndicator'),
    streamingIndicatorText: document.getElementById('streamingIndicatorText'),
    chatModelLabel: document.getElementById('chatModelLabel'),
    newChatBtn: document.getElementById('newChatBtn'),
    chatInput: document.getElementById('chatInput'),
    sendBtn: document.getElementById('sendBtn'),
    attachBtn: document.getElementById('attachBtn'),
    fileInput: document.getElementById('fileInput'),
    attachmentPreview: document.getElementById('attachmentPreview'),
    attachmentList: document.getElementById('attachmentList'),
    toastContainer: document.getElementById('toastContainer'),
    sidebar: document.getElementById('sidebar'),
    sidebarToggle: document.getElementById('sidebarToggle'),
    traceToggle: document.getElementById('traceToggle'),
    tracePanel: document.getElementById('tracePanel'),
    traceLog: document.getElementById('traceLog'),
    traceClearBtn: document.getElementById('traceClearBtn'),
    tracePauseBtn: document.getElementById('tracePauseBtn'),
};
"""


def _start_server():
    """Start the server in a subprocess. Returns the process handle."""
    env = os.environ.copy()
    env["LM_CONSOLE_PORT"] = str(SERVER_PORT)

    # Find the virtualenv Python
    venv_python = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), ".venv", "bin", "python"
    )
    if not os.path.exists(venv_python):
        venv_python = sys.executable

    proc = subprocess.Popen(
        [venv_python, "run.py"],
        cwd=os.path.join(os.path.dirname(os.path.dirname(__file__))),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Wait for server to be ready (check stdout for startup message)
    for _ in range(30):
        time.sleep(0.5)
        if proc.poll() is not None:
            # Process exited - check output for errors
            stdout = proc.stdout.read().decode()
            proc.stdout.close()
            raise RuntimeError(f"Server failed to start: {stdout}")
        # Check if server is ready by looking at buffered output
        try:
            output = proc.stdout.readline().decode()
            if "Uvicorn running" in output:
                return proc
        except Exception:
            pass

    proc.kill()
    proc.wait()
    raise RuntimeError("Server failed to start within timeout")


def _stop_server(proc):
    """Stop the server gracefully."""
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    # Close stdout to avoid ResourceWarning
    try:
        proc.stdout.close()
    except Exception:
        pass


class TestScreenshot(unittest.TestCase):
    """Playwright screenshot validation tests."""

    @classmethod
    def setUpClass(cls):
        """Start the server before all tests."""
        cls.server_proc = _start_server()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        """Stop the server and clean up after all tests."""
        cls.browser.close()
        cls.playwright.stop()
        _stop_server(cls.server_proc)

    def setUp(self):
        """Create a new page for each test."""
        self.page = self.browser.new_page()
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    def tearDown(self):
        """Clean up after each test to ensure isolation."""
        try:
            self.page.evaluate("localStorage.clear()")
        except Exception:
            pass
        self.page.close()

    def _screenshot(self, name):
        """Take a screenshot and save it."""
        path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
        self.page.screenshot(path=path)
        return path

    def _navigate(self):
        """Navigate to the app and wait for full load."""
        self.page.goto(BASE_URL)
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(500)

    def _eval_with_dom(self, js_code):
        """Evaluate JavaScript with the DOM helper object injected.

        Wraps page.evaluate() so the DOM_JS template is always prepended,
        avoiding error-prone string concatenation in test methods.
        """
        self.page.evaluate(
            f"""async () => {{
                {DOM_JS}
                {js_code}
            }}"""
        )

    def _install_chat_hold_mock(self):
        """Mock /api/chat so an in-flight chat request stays pending until cancelled.

        Installs two cooperating pieces:
          * A ``page.route`` handler for /api/chat that fulfills immediately with
            a minimal SSE response. Fulfilling (rather than leaving the route
            pending) lets Playwright's route task complete cleanly, avoiding
            the asyncio "Task was destroyed" / CancelledError noise that a
            never-fulfilled route would log at teardown.
          * An in-page fetch wrapper that, for /api/chat, drives the request
            through the route mock (so /api/chat is mocked via page.route) but
            returns a promise that only settles when the request's AbortSignal
            fires. This keeps the caller (apiCallChat -> sendMessage) in-flight
            until the client cancels, and records the abort in
            ``window.__chatFetchAborted`` so tests can assert cancellation.

        Callers must ``page.unroute('**/api/chat')`` when done (e.g. in finally).
        """
        self.page.route(
            "**/api/chat",
            lambda route: route.fulfill(
                status=200,
                headers={"Content-Type": "text/event-stream"},
                body="data: [DONE]\n\n",
            ),
        )
        self.page.evaluate(
            """() => {
                window.__chatFetchAborted = false;
                const origFetch = window.fetch;
                const makeAbortError = () => {
                    const err = new Error('The operation was aborted.');
                    err.name = 'AbortError';
                    return err;
                };
                window.fetch = function(...args) {
                    const [url, opts] = args;
                    if (typeof url === 'string' && url === '/api/chat') {
                        const signal = opts && opts.signal;
                        // Drive the request through the page.route mock so
                        // the route handler runs and completes cleanly. The
                        // response is intentionally discarded; the caller is
                        // held in-flight by the promise below.
                        origFetch.apply(this, args).catch(() => {});
                        return new Promise((resolve, reject) => {
                            if (!signal) return; // no signal: stay in-flight
                            if (signal.aborted) {
                                window.__chatFetchAborted = true;
                                reject(makeAbortError());
                                return;
                            }
                            signal.addEventListener('abort', () => {
                                window.__chatFetchAborted = true;
                                reject(makeAbortError());
                            });
                        });
                    }
                    return origFetch.apply(this, args);
                };
            }"""
        )

    def _enable_chat_ui_for_test_model(self):
        """Inject a selected model into state and enable the chat input/buttons.

        Mirrors the setup used by test_send_message_interactive so that
        sendMessage() proceeds past its early-return guards.
        """
        self.page.evaluate(
            """async () => {
                const mod = await import('/static/js/state.js');
                mod.state.selectedModel = 'test-model';
            }"""
        )
        self.page.evaluate(
            """() => {
                document.getElementById('chatInput').disabled = false;
                document.getElementById('sendBtn').disabled = false;
                const attachBtn = document.getElementById('attachBtn');
                if (attachBtn) attachBtn.disabled = false;
                document.getElementById('emptyState').style.display = 'none';
                document.getElementById('chatHeader').style.display = 'flex';
                document.getElementById('chatMessages').style.display = 'flex';
                document.getElementById('chatMetrics').style.display = 'flex';
            }"""
        )

    def _click_send_btn(self):
        """Click the send button at an offset that avoids the cursor-indicator.

        The send button shares its input wrapper with a positioned
        .cursor-indicator overlay that can intercept pointer events at the
        button's default center click point (see _SEND_BTN_CLICK_POS). This
        clicks the left portion of the button, which is always clear.
        """
        self.page.locator("#sendBtn").click(position=_SEND_BTN_CLICK_POS)

    # --- Visual tests (existing) ---

    def test_page_loads(self):
        """SPEC: Page renders with all UI elements."""
        self._navigate()

        # Verify key UI elements exist (use is_visible for visible elements,
        # is_hidden check for hidden elements)
        self.assertTrue(self.page.locator("#endpoint").is_visible())
        self.assertTrue(self.page.locator("#connectBtn").is_visible())
        self.assertTrue(self.page.locator("#modelList").is_visible())
        self.assertTrue(self.page.locator("#chatMessages").is_visible())
        self.assertTrue(self.page.locator("#chatInput").is_visible())
        self.assertTrue(self.page.locator("#sendBtn").is_visible())
        self.assertTrue(self.page.locator("#statusDot").is_visible())

        # Toast container exists but is hidden (no toasts yet)
        toast = self.page.locator("#toastContainer")
        self.assertIsNotNone(toast)

        # Verify page title
        self.assertEqual(self.page.title(), "LM Studio Console")

        self._screenshot("01_page_loads")

    def test_status_disconnected(self):
        """SPEC: Default status shows disconnected."""
        self._navigate()

        status_text = self.page.text_content("#statusText")
        self.assertEqual(status_text, "Disconnected")

        # Status dot exists (no 'connected' class by default)
        dot_class = self.page.locator("#statusDot").get_attribute("class")
        self.assertNotEqual(dot_class, "connected")

        self._screenshot("02_status_disconnected")

    def test_sidebar_sections(self):
        """SPEC: Sidebar has Connection, Models, History, Settings sections."""
        self._navigate()

        # Verify sidebar sections
        self.page.wait_for_selector("text=Connection")
        self.page.wait_for_selector("text=Models")
        self.page.wait_for_selector("text=Session History")
        self.page.wait_for_selector("text=Chat Settings")

        self._screenshot("03_sidebar_sections")

    def test_settings_panel(self):
        """SPEC: Settings panel has system prompt, temperature, tool call toggle."""
        self._navigate()

        # Open settings panel
        self.page.click("#settingsToggle")
        self.page.wait_for_selector("#settingsPanel.open", timeout=5000)

        # Verify settings elements are visible after panel opens
        self.assertTrue(self.page.locator("#systemPrompt").is_visible())
        self.assertTrue(self.page.locator("#temperature").is_visible())
        # toolCallToggle checkbox is hidden by CSS (uses custom toggle slider)
        # Check the toggle slider is visible instead
        self.assertTrue(self.page.locator(".toggle-slider").is_visible())

        self._screenshot("04_settings_panel")

    def test_history_panel(self):
        """SPEC: History panel is present and collapsible."""
        self._navigate()

        # Open history panel
        self.page.click("#historyToggle")
        self.page.wait_for_selector("#historyPanel.open", timeout=5000)

        # Verify history elements
        self.assertTrue(self.page.locator("#historyList").is_visible())

        self._screenshot("05_history_panel")

    def test_chat_input_area(self):
        """SPEC: Chat input has textarea, send button, attach button."""
        self._navigate()

        # Verify chat input elements
        chat_input = self.page.locator("#chatInput")
        self.assertTrue(chat_input.is_visible())

        send_btn = self.page.locator("#sendBtn")
        self.assertTrue(send_btn.is_visible())

        attach_btn = self.page.locator("#attachBtn")
        self.assertTrue(attach_btn.is_visible())

        # Verify placeholder text
        placeholder = chat_input.get_attribute("placeholder")
        self.assertIsNotNone(placeholder)

        self._screenshot("06_chat_input_area")

    def test_chat_metrics_hidden(self):
        """SPEC: Metrics bar is hidden by default."""
        self._navigate()

        metrics = self.page.locator("#chatMetrics")
        self.assertTrue(metrics.is_hidden())

        self._screenshot("07_metrics_hidden")

    def test_new_chat_button(self):
        """SPEC: New Chat button is present."""
        self._navigate()

        new_chat_btn = self.page.locator("#newChatBtn")
        self.assertTrue(new_chat_btn.is_visible())
        self.assertEqual(new_chat_btn.inner_text(), "New Chat")

        self._screenshot("08_new_chat_button")

    def test_model_buttons(self):
        """SPEC: Refresh, Load, Unload model buttons are present."""
        self._navigate()

        self.assertTrue(self.page.locator("#refreshModelsBtn").is_visible())
        self.assertTrue(self.page.locator("#loadModelBtn").is_visible())
        self.assertTrue(self.page.locator("#unloadModelBtn").is_visible())

        self._screenshot("09_model_buttons")

    def test_toast_container(self):
        """SPEC: Toast container exists for notifications."""
        self._navigate()

        # Toast container exists in DOM (hidden when empty)
        toast_container = self.page.locator("#toastContainer")
        self.assertIsNotNone(toast_container)

        self._screenshot("10_toast_container")

    # --- Interactive behavioral tests ---
    #
    # Behavioral tests inject state directly via page.evaluate() to simulate
    # API responses and state transitions. This bypasses actual backend calls
    # (e.g., connection, model loading, chat), making tests fast and deterministic.
    # The injected state exercises the same frontend code paths as real usage.

    def test_connect_button_interactive(self):
        """Interactive: Click Connect, verify state transitions on connection failure."""
        self._navigate()

        # Verify initial disconnected state
        self.assertEqual(self.page.text_content("#statusText"), "Disconnected")
        self.assertEqual(self.page.text_content("#connectBtn"), "Connect")

        # Click Connect button
        self.page.click("#connectBtn")

        # Verify connecting state
        self.page.wait_for_selector(
            "#statusText:has-text('Connecting...')", timeout=10000
        )
        self.assertEqual(self.page.text_content("#connectBtn"), "Connecting...")
        self.assertTrue(self.page.locator("#connectBtn").is_disabled())

        # Wait for connection to fail — detect by status reverting to Disconnected.
        # Toast auto-dismisses after 3.5s so we must catch it while still visible.
        self.page.wait_for_function(
            "() => document.getElementById('statusText').textContent === 'Disconnected'",
            timeout=20000,
        )

        # Verify back to disconnected state
        self.assertEqual(self.page.text_content("#statusText"), "Disconnected")
        self.assertEqual(self.page.text_content("#connectBtn"), "Connect")
        self.assertFalse(self.page.locator("#connectBtn").is_disabled())

        # Verify error toast is still visible (check immediately after failure detected)
        toast = self.page.locator(".toast.error")
        self.assertTrue(
            toast.is_visible(),
            "Error toast should be visible after failed connection",
        )
        self.assertIn("Connection failed", toast.text_content())

        self._screenshot("11_connect_button_interactive")

    def test_send_message_interactive(self):
        """Interactive: Type and send a message, verify UI flow (user msg, streaming, error)."""
        self._navigate()

        # Inject a model into state so sendMessage doesn't return early
        self.page.evaluate(
            """async () => {
                const mod = await import('/static/js/state.js');
                mod.state.selectedModel = 'test-model';
            }"""
        )

        # Enable input/send buttons and show chat area
        self.page.evaluate(
            """() => {
                document.getElementById('chatInput').disabled = false;
                document.getElementById('sendBtn').disabled = false;
                const attachBtn = document.getElementById('attachBtn');
                if (attachBtn) attachBtn.disabled = false;
                document.getElementById('emptyState').style.display = 'none';
                document.getElementById('chatHeader').style.display = 'flex';
                document.getElementById('chatMessages').style.display = 'flex';
                document.getElementById('chatMetrics').style.display = 'flex';
            }"""
        )

        # Type a message
        self.page.fill("#chatInput", "Hello, this is a test message")

        # Click send button
        self.page.click("#sendBtn")

        # Verify user message appears in chat
        self.page.wait_for_selector(".message.user", timeout=10000)
        user_text = self.page.locator(".message.user .message-text")
        self.assertIn("Hello", user_text.text_content())

        # Verify streaming indicator is visible
        self.assertTrue(self.page.locator("#streamingIndicator").is_visible())

        # Verify assistant placeholder is created
        self.page.wait_for_selector(".message.assistant", timeout=10000)

        # Wait for error by detecting streaming indicator becoming hidden.
        # Toast auto-dismisses after 3.5s so we must catch it while still visible.
        # Proxy streaming has no timeout; connection failure takes time.
        self.page.wait_for_function(
            "() => document.getElementById('streamingIndicator').style.display === 'none'",
            timeout=20000,
        )

        # Verify streaming indicator is hidden after error
        self.assertTrue(self.page.locator("#streamingIndicator").is_hidden())

        # Verify error message element is shown
        error_el = self.page.locator(".error-message")
        self.assertTrue(error_el.is_visible(), "Error message should be visible")

        # Verify error toast is still visible (check immediately after error detected)
        toast = self.page.locator(".toast.error")
        self.assertTrue(toast.is_visible(), "Error toast should be visible")

        self._screenshot("12_send_message_interactive")

    def test_new_chat_button_interactive(self):
        """Interactive: Click New Chat, verify chat clears and session is saved."""
        self._navigate()

        # Inject a message into state and render it using real appendMessage()
        self.page.evaluate(
            """async () => {
                const stateMod = await import('/static/js/state.js');
                const chatMod = await import('/static/js/chat.js');
                const msg = { role: 'user', content: 'Test message for new chat' };
                stateMod.state.chatMessages.push(msg);

                document.getElementById('emptyState').style.display = 'none';
                document.getElementById('chatHeader').style.display = 'flex';
                document.getElementById('chatMessages').style.display = 'flex';
                document.getElementById('chatMetrics').style.display = 'flex';

                const dom = { chatMessages: document.getElementById('chatMessages') };
                chatMod.appendMessage(dom, msg, 'user');
            }"""
        )

        # Verify message is visible
        self.assertTrue(self.page.locator(".message.user").is_visible())

        # Click New Chat button
        self.page.click("#newChatBtn")

        # Verify chat area is cleared
        self.assertEqual(self.page.locator("#chatMessages").inner_html(), "")

        # Verify streaming indicator hidden
        self.assertTrue(self.page.locator("#streamingIndicator").is_hidden())

        # Verify metrics hidden
        self.assertTrue(self.page.locator("#chatMetrics").is_hidden())

        # Verify toast appears
        toast = self.page.locator(".toast.info")
        self.assertTrue(toast.is_visible(), "Toast should be visible")
        self.assertIn("Chat cleared", toast.text_content())

        # Verify session was saved to history
        history_count = self.page.evaluate(
            "() => document.querySelectorAll('.history-item').length"
        )
        self.assertGreater(
            history_count, 0, "History should contain the saved session"
        )

        self._screenshot("13_new_chat_interactive")

    def test_settings_toggle_interactive(self):
        """Interactive: Click settings toggle, verify panel opens and closes."""
        self._navigate()

        # Initial: settings panel closed
        self.assertTrue(self.page.locator("#settingsPanel").is_hidden())

        # Click to open
        self.page.click("#settingsToggle")
        self.page.wait_for_selector("#settingsPanel.open", timeout=5000)

        # Verify panel is open with elements visible
        self.assertTrue(self.page.locator("#settingsPanel").is_visible())
        self.assertTrue(self.page.locator("#systemPrompt").is_visible())
        self.assertTrue(self.page.locator("#temperature").is_visible())

        self._screenshot("14a_settings_open")

        # Click to close
        self.page.click("#settingsToggle")
        self.page.wait_for_timeout(500)

        # Verify panel is closed
        self.assertTrue(self.page.locator("#settingsPanel").is_hidden())

        self._screenshot("14b_settings_closed")

    def test_model_select_and_load_interactive(self):
        """Interactive: Select a model, click Load/Unload, verify state feedback."""
        self._navigate()

        # Inject model into state, render model list, and update status
        self._eval_with_dom(
            """
                const stateMod = await import('/static/js/state.js');
                const modelMod = await import('/static/js/models.js');
                const connMod = await import('/static/js/connection.js');

                stateMod.state.connected = true;
                stateMod.state.models = [{
                    key: 'test-model',
                    type: 'llm',
                    display_name: 'test-model',
                    loaded_instances: []
                }];
                stateMod.state.isLmStudioEndpoint = true;

                modelMod.renderModelList(dom);
                connMod.updateStatus(dom, true);
            """
        )

        # Verify model appears in list
        self.page.wait_for_selector(".model-item", timeout=10000)

        # Click to select the model
        self.page.click(".model-item")
        self.page.wait_for_timeout(500)

        # Verify model is selected (has .selected class)
        self.assertTrue(self.page.locator(".model-item.selected").is_visible())

        # Click Load Model button
        self.page.click("#loadModelBtn")

        # Verify loading state feedback
        self.page.wait_for_selector(
            "#statusText:has-text('Loading model...')", timeout=10000
        )
        self.assertEqual(self.page.text_content("#loadModelBtn"), "Loading...")
        self.assertTrue(self.page.locator("#loadModelBtn").is_disabled())

        self._screenshot("15a_model_loading")

        # Simulate model becoming loaded (backend op has 600s timeout; skip waiting)
        self._eval_with_dom(
            """
                const stateMod = await import('/static/js/state.js');
                const modelMod = await import('/static/js/models.js');
                const connMod = await import('/static/js/connection.js');

                // Restore button state
                document.getElementById('loadModelBtn').textContent = 'Load';
                document.getElementById('loadModelBtn').disabled = false;

                // Mark model as loaded
                const model = stateMod.state.models.find(m => m.key === 'test-model');
                if (model) model.loaded_instances = [{ id: 'inst-1' }];
                stateMod.state.loadedModels.add('inst-1');
                stateMod.state.selectedModel = 'test-model';

                modelMod.renderModelList(dom);
                connMod.updateStatus(dom, true);
                connMod.enableChatControls(dom);
            """
        )

        # Verify model shows as loaded
        self.assertTrue(
            self.page.locator(".model-item.loaded").is_visible(),
            "Model should show loaded badge",
        )

        # Click Unload Model button
        self.page.click("#unloadModelBtn")

        # Verify unloading state feedback
        self.page.wait_for_selector(
            "#statusText:has-text('Unloading model...')", timeout=10000
        )
        self.assertEqual(self.page.text_content("#unloadModelBtn"), "Unloading...")
        self.assertTrue(self.page.locator("#unloadModelBtn").is_disabled())

        self._screenshot("15b_model_unloading")

        # Simulate unload completing (backend op has 600s timeout; skip waiting)
        self._eval_with_dom(
            """
                const stateMod = await import('/static/js/state.js');
                const modelMod = await import('/static/js/models.js');
                const connMod = await import('/static/js/connection.js');

                // Restore button state
                document.getElementById('unloadModelBtn').textContent = 'Unload';
                document.getElementById('unloadModelBtn').disabled = false;

                // Mark model as unloaded
                const model = stateMod.state.models.find(m => m.key === 'test-model');
                if (model) model.loaded_instances = [];
                stateMod.state.loadedModels.delete('inst-1');
                stateMod.state.selectedModel = null;
                stateMod.state.streaming = false;

                modelMod.renderModelList(dom);
                connMod.updateStatus(dom, true);
                connMod.enableChatControls(dom);
            """
        )

        # Verify model no longer shows as loaded
        self.assertFalse(
            self.page.locator(".model-item.loaded").is_visible(),
            "Model should not show loaded badge after unload",
        )

        self._screenshot("15c_model_unloaded")

    def test_copy_button_interactive(self):
        """Interactive: Click Copy button on assistant message, verify text change and revert."""
        self._navigate()

        # Create an assistant message with copy button using the real appendMessage function
        self.page.evaluate(
            """async () => {
                const chatMod = await import('/static/js/chat.js');
                const stateMod = await import('/static/js/state.js');

                stateMod.state.chatMessages.push({ role: 'assistant', content: 'This is a test response' });

                document.getElementById('emptyState').style.display = 'none';
                document.getElementById('chatHeader').style.display = 'flex';
                document.getElementById('chatMessages').style.display = 'flex';

                const dom = { chatMessages: document.getElementById('chatMessages') };
                chatMod.appendMessage(dom, { role: 'assistant', content: 'This is a test response' }, 'assistant');
            }"""
        )

        # Verify assistant message and copy button exist
        self.page.wait_for_selector(".message.assistant .copy-btn", timeout=10000)

        # Verify initial button text
        copy_btn = self.page.locator(".message.assistant .copy-btn")
        self.assertEqual(copy_btn.text_content(), "Copy")

        # Click copy button
        self.page.click(".message.assistant .copy-btn")

        # Wait briefly for DOM update
        self.page.wait_for_timeout(500)

        # Verify button text changes to "Copied!"
        self.assertEqual(copy_btn.text_content(), "Copied!")
        btn_class = copy_btn.get_attribute("class")
        self.assertIn("copied", btn_class)

        # Wait for revert (2s timeout in handler + buffer)
        self.page.wait_for_timeout(2500)

        # Verify button text reverts to "Copy"
        self.assertEqual(copy_btn.text_content(), "Copy")
        btn_class = copy_btn.get_attribute("class")
        self.assertNotIn("copied", btn_class)

        self._screenshot("16_copy_button_interactive")

    # --- Cancellation (stop button) interactive tests ---

    def test_stop_button_interactive(self):
        """Interactive: Send a message, then click the stop button to cancel.

        Verifies the send button swaps to stop mode during streaming, and that
        clicking it cancels the in-flight request: the streaming indicator
        hides, a 'Cancelled' toast appears, the button returns to send mode,
        and the underlying /api/chat fetch is aborted.
        """
        self._navigate()
        self._enable_chat_ui_for_test_model()
        self._install_chat_hold_mock()

        try:
            # Type a message and click send
            self.page.fill("#chatInput", "Hello, please respond slowly")
            self._click_send_btn()

            # Once streaming starts, the send button should be in stop mode
            self.page.wait_for_selector(
                "#streamingIndicator", state="visible", timeout=10000
            )
            self.assertTrue(
                self.page.locator("#sendBtn").evaluate(
                    "el => el.classList.contains('stop-mode')"
                ),
                "Send button should have stop-mode class during streaming",
            )
            self.assertFalse(
                self.page.evaluate("() => window.__chatFetchAborted === true"),
                "Fetch should still be in-flight before cancel",
            )

            # Click the send button again (now in stop mode) to trigger cancel
            self._click_send_btn()

            # Streaming indicator should hide
            self.page.wait_for_selector(
                "#streamingIndicator", state="hidden", timeout=10000
            )

            # A 'Cancelled' toast should appear (info toast)
            self.page.wait_for_selector(
                ".toast.info:has-text('Cancelled')", timeout=10000
            )
            self.assertIn(
                "Cancelled", self.page.locator(".toast.info").first.text_content()
            )

            # Send button should return to send mode (no stop-mode class)
            self.assertFalse(
                self.page.locator("#sendBtn").evaluate(
                    "el => el.classList.contains('stop-mode')"
                ),
                "Send button should return to send mode after cancel",
            )

            # The in-flight fetch should have been aborted (signal abort fired)
            self.assertTrue(
                self.page.evaluate("() => window.__chatFetchAborted === true"),
                "The in-flight /api/chat fetch should have been aborted",
            )

            self._screenshot("17_stop_button_interactive")
        finally:
            self.page.unroute("**/api/chat")

    # --- Theme tests ---

    def test_theme_selector_visible(self):
        """Verify the theme select dropdown exists in the settings panel."""
        self._navigate()

        # Open settings panel to reveal theme selector
        self.page.click("#settingsToggle")
        self.page.wait_for_selector("#settingsPanel.open", timeout=5000)

        # Verify theme select exists and is visible
        theme_select = self.page.locator("#themeSelect")
        self.assertTrue(theme_select.is_visible(),
                        "Theme selector should be visible in settings")

        # Verify options
        options = self.page.locator("#themeSelect option").all()
        self.assertEqual(len(options), 3,
                         "Theme selector should have 3 options")
        values = [opt.get_attribute("value") for opt in options]
        self.assertIn("cyberpunk", values)
        self.assertIn("light", values)
        self.assertIn("warm", values)

        # Verify default is cyberpunk (select.value requires JS evaluation)
        default_value = self.page.evaluate(
            "document.getElementById('themeSelect').value")
        self.assertEqual(default_value, "cyberpunk")

        self._screenshot("19_theme_selector_visible")

    def test_toggle_theme_light(self):
        """Switch to Light Professional theme, verify CSS variables changed."""
        self._navigate()

        # Open settings panel
        self.page.click("#settingsToggle")
        self.page.wait_for_selector("#settingsPanel.open", timeout=5000)

        # Switch to light theme
        self.page.select_option("#themeSelect", "light")
        self.page.wait_for_timeout(500)

        # Verify stylesheet href changed
        href = self.page.evaluate("document.getElementById('theme-stylesheet').href")
        self.assertIn("theme-light.css", href, "Stylesheet should be theme-light.css")

        # Verify CSS variables changed (light theme uses different palette)
        bg_base = self.page.evaluate("getComputedStyle(document.body).getPropertyValue('--bg-base')")
        self.assertIn("#FAFAF8", bg_base, "Light theme background should be #FAFAF8")

        # Verify theme state persisted
        saved = self.page.evaluate("localStorage.getItem('lm_console_settings')")
        settings = json.loads(saved)
        self.assertEqual(settings["theme"], "light")

        self._screenshot("20_toggle_theme_light")

    def test_toggle_theme_warm(self):
        """Switch to Warm Minimal theme, verify CSS variables changed."""
        self._navigate()

        # Open settings panel
        self.page.click("#settingsToggle")
        self.page.wait_for_selector("#settingsPanel.open", timeout=5000)

        # Switch to warm theme
        self.page.select_option("#themeSelect", "warm")
        self.page.wait_for_timeout(500)

        # Verify stylesheet href changed
        href = self.page.evaluate("document.getElementById('theme-stylesheet').href")
        self.assertIn("theme-warm.css", href, "Stylesheet should be theme-warm.css")

        # Verify CSS variables changed (warm theme uses different palette)
        bg_base = self.page.evaluate("getComputedStyle(document.body).getPropertyValue('--bg-base')")
        self.assertIn("#F5F0E8", bg_base, "Warm theme background should be #F5F0E8")

        # Verify theme state persisted
        saved = self.page.evaluate("localStorage.getItem('lm_console_settings')")
        settings = json.loads(saved)
        self.assertEqual(settings["theme"], "warm")

        self._screenshot("21_toggle_theme_warm")

    def test_theme_persistence(self):
        """Switch theme, reload page, verify theme persists via localStorage."""
        self._navigate()

        # Open settings panel and switch to light theme
        self.page.click("#settingsToggle")
        self.page.wait_for_selector("#settingsPanel.open", timeout=5000)
        self.page.select_option("#themeSelect", "light")
        self.page.wait_for_timeout(500)

        # Verify theme was saved
        saved = self.page.evaluate("localStorage.getItem('lm_console_settings')")
        settings = json.loads(saved)
        self.assertEqual(settings["theme"], "light")

        # Reload page
        self.page.reload()
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(1000)

        # Verify theme persisted: stylesheet href and select value
        href = self.page.evaluate("document.getElementById('theme-stylesheet').href")
        self.assertIn("theme-light.css", href, "Stylesheet should persist as theme-light.css")

        select_value = self.page.evaluate("document.getElementById('themeSelect').value")
        self.assertEqual(select_value, "light", "Select value should persist as light")

        # Verify CSS variables reflect light theme
        bg_base = self.page.evaluate("getComputedStyle(document.body).getPropertyValue('--bg-base')")
        self.assertIn("#FAFAF8", bg_base, "Light theme background should persist")

        self._screenshot("22_theme_persistence")

    def test_trace_log_panel(self):
        """Open trace log panel, verify it renders and is collapsible."""
        self._navigate()

        # Verify trace toggle button exists
        trace_toggle = self.page.locator("#traceToggle")
        self.assertTrue(trace_toggle.is_visible(), "Trace toggle button should be visible")

        # Verify trace panel is closed by default
        self.assertTrue(self.page.locator("#tracePanel").is_hidden(), "Trace panel should be closed by default")

        # Open trace log panel
        self.page.click("#traceToggle")
        self.page.wait_for_selector("#tracePanel.open", timeout=5000)

        # Verify panel is open
        self.assertTrue(self.page.locator("#tracePanel").is_visible(), "Trace panel should be visible when opened")

        # Verify trace log container and controls exist
        self.assertTrue(self.page.locator("#traceLog").is_visible(), "Trace log container should be visible")
        self.assertTrue(self.page.locator("#traceClearBtn").is_visible(), "Clear button should be visible")
        self.assertTrue(self.page.locator("#tracePauseBtn").is_visible(), "Pause button should be visible")

        # Verify pause button text
        pause_text = self.page.text_content("#tracePauseBtn")
        self.assertEqual(pause_text, "Pause")

        self._screenshot("23a_trace_panel_open")

        # Close trace log panel
        self.page.click("#traceToggle")
        self.page.wait_for_timeout(500)

        # Verify panel is closed
        self.assertTrue(self.page.locator("#tracePanel").is_hidden(), "Trace panel should be closed after toggle")

        self._screenshot("23b_trace_panel_closed")

    # --- Cancellation (stop button) interactive tests ---

    def test_new_chat_cancels_request(self):
        """Interactive: Starting a new chat aborts an in-flight request.

        While a chat request is hung in-flight (mocked /api/chat), clicking New
        Chat should abort the underlying fetch. Verified via the in-page abort
        flag set when the mocked fetch's AbortSignal fires, plus the chat area
        being cleared and a 'Chat cleared' toast.
        """
        self._navigate()
        self._enable_chat_ui_for_test_model()
        self._install_chat_hold_mock()

        try:
            # Start a send (the fetch will hang in-flight)
            self.page.fill("#chatInput", "Hello, this request should be cancelled")
            self._click_send_btn()

            # Confirm the request is in-flight
            self.page.wait_for_selector(
                "#streamingIndicator", state="visible", timeout=10000
            )
            self.assertFalse(
                self.page.evaluate("() => window.__chatFetchAborted === true"),
                "Fetch should still be in-flight before new chat",
            )

            # Click New Chat — this should abort the in-flight request
            self.page.click("#newChatBtn")

            # Assert the stuck fetch was aborted (signal abort fired)
            self.page.wait_for_function(
                "() => window.__chatFetchAborted === true", timeout=10000
            )
            self.assertTrue(
                self.page.evaluate("() => window.__chatFetchAborted === true"),
                "New chat should abort the in-flight /api/chat fetch",
            )

            # Streaming indicator should be hidden after new chat
            self.assertTrue(self.page.locator("#streamingIndicator").is_hidden())

            # Chat area should be cleared
            self.assertEqual(self.page.locator("#chatMessages").inner_html(), "")

            # New Chat toast ('Chat cleared') should appear
            self.page.wait_for_selector(
                ".toast.info:has-text('Chat cleared')", timeout=10000
            )

            self._screenshot("18_new_chat_cancels_request")
        finally:
            self.page.unroute("**/api/chat")


if __name__ == "__main__":
    unittest.main()
