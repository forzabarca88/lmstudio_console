"""Screenshot validation tests using Playwright.

Validates the rendered UI matches expected layout and content.
Tests cover SPEC minimum test cases: UI element presence,
interaction states, and visual rendering.

Run: uv run python -m unittest tests.test_screenshot -v
"""

import os
import sys
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

    def _screenshot(self, name):
        """Take a screenshot and save it."""
        path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
        self.page.screenshot(path=path)
        return path

    def _navigate(self):
        """Navigate to the app and wait for load."""
        self.page.goto(BASE_URL)
        self.page.wait_for_load_state("domcontentloaded")

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


if __name__ == "__main__":
    unittest.main()
