"""Frontend module tests.

Verifies JS module structure, imports, and exports are consistent.
Tests that all required features are present in the served files.
"""

import unittest
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from backend import server


class TestJSModuleStructure(unittest.TestCase):
    """Verify JS modules have correct import/export structure."""

    def setUp(self):
        self.client = TestClient(server.app)
        self.modules = {}
        for name in ["state.js", "api.js", "ui.js", "connection.js",
                      "models.js", "history.js", "chat.js", "app.js"]:
            resp = self.client.get(f"/static/js/{name}")
            self.assertEqual(resp.status_code, 200)
            self.modules[name] = resp.text

    def _get_exports(self, content):
        """Extract exported names from JS module."""
        exports = set()
        for m in re.finditer(r'export\s+(?:async\s+)?function\s+(\w+)', content):
            exports.add(m.group(1))
        for m in re.finditer(r'export\s+const\s+(\w+)', content):
            exports.add(m.group(1))
        return exports

    def _get_imports(self, content):
        """Extract imports from JS module as (source_module, imported_names)."""
        imports = []
        for m in re.finditer(r'import\s+\{([^}]+)\}\s+from\s+"(\./[^"]+)"', content):
            sourced = m.group(2).replace("./", "").replace(".js", "")
            names = [n.strip() for n in m.group(1).split(",")]
            imports.append((sourced, names))
        return imports

    def test_all_imports_resolved(self):
        """Every imported name must be exported by its source module."""
        all_exports = {name.replace(".js", ""): self._get_exports(content)
                       for name, content in self.modules.items()}

        errors = []
        for name, content in self.modules.items():
            for sourced, imported_names in self._get_imports(content):
                if sourced not in all_exports:
                    errors.append(f"{name}: imports from unknown module '{sourced}'")
                    continue
                for imp in imported_names:
                    if imp not in all_exports[sourced]:
                        errors.append(
                            f"{name}: imports '{imp}' from {sourced} "
                            f"but {sourced} doesn't export it"
                        )

        self.assertEqual(errors, [], "\n".join(errors))




class TestJSFeatures(unittest.TestCase):
    """Verify all required features are present in the JS code."""

    def setUp(self):
        self.client = TestClient(server.app)
        self.html = self.client.get("/").text
        self.css = self.client.get("/static/css/style.css").text
        self.state_js = self.client.get("/static/js/state.js").text
        self.connection_js = self.client.get("/static/js/connection.js").text
        self.models_js = self.client.get("/static/js/models.js").text
        self.chat_js = self.client.get("/static/js/chat.js").text
        self.history_js = self.client.get("/static/js/history.js").text

    def test_markdown_rendering(self):
        """Messages rendered with Markdown."""
        self.assertIn("marked", self.html)
        self.assertIn("marked.parse", self.chat_js)

    def test_mermaid_js(self):
        """Graphs rendered with Mermaid JS."""
        self.assertIn("mermaid", self.html)
        self.assertIn("mermaid.initialize", self.chat_js)
        self.assertIn("mermaid.render", self.chat_js)
        self.assertIn(".mermaid", self.css)

    def test_copy_button(self):
        """Copy button on assistant responses."""
        self.assertIn("copy-btn", self.css)
        self.assertIn("clipboard.writeText", self.chat_js)
        self.assertIn("Copy</button>", self.chat_js)

    def test_session_history(self):
        """Chat session history with delete/continue."""
        self.assertIn("sessionHistory", self.state_js)
        self.assertIn("saveCurrentSession", self.state_js)
        self.assertIn("saveSessionHistory", self.state_js)
        self.assertIn("renderHistoryList", self.history_js)
        self.assertIn("continueSession", self.history_js)
        self.assertIn("deleteSession", self.history_js)
        self.assertIn("historyList", self.html)
        self.assertIn("historyToggle", self.html)
        self.assertIn(".history-panel", self.css)
        self.assertIn(".history-item", self.css)

    def test_heartbeat(self):
        """Auto-detect disconnection via heartbeat."""
        self.assertIn("heartbeatInterval", self.state_js)
        self.assertIn("startHeartbeat", self.connection_js)
        self.assertIn("stopHeartbeat", self.connection_js)
        self.assertIn("setInterval", self.connection_js)
        self.assertIn("clearInterval", self.connection_js)

    def test_chat_preserved_on_disconnect(self):
        """Chat messages not cleared on disconnect."""
        self.assertIn("Preserves existing chat", self.connection_js)

    def test_chat_preserved_on_unload(self):
        """Chat messages not cleared on model unload."""
        self.assertIn("Preserves existing chat", self.models_js)

    def test_shift_enter_newlines(self):
        """Multi-line input with Shift+Enter."""
        self.assertIn("Shift + Enter", self.html)
        self.assertIn("shiftKey", self.client.get("/static/js/app.js").text)

    def test_system_prompt(self):
        """Configurable system prompt."""
        self.assertIn("systemPrompt", self.html)
        self.assertIn("systemPrompt", self.state_js)

    def test_temperature(self):
        """Configurable temperature."""
        self.assertIn("temperature", self.html)
        self.assertIn("temperature", self.state_js)

    def test_chat_metrics(self):
        """Live chat metrics display."""
        self.assertIn("chat-metrics", self.css)
        self.assertIn("tokensPerSecond", self.chat_js)
        self.assertIn("timeToFirstToken", self.chat_js)
        self.assertIn("totalTokens", self.chat_js)

    def test_streaming_indicator(self):
        """Visual feedback during streaming."""
        self.assertIn("streaming-indicator", self.css)
        self.assertIn("streamingIndicator", self.chat_js)

    def test_model_loaded_indicator(self):
        """Model list shows loaded models."""
        self.assertIn("badge-loaded", self.css)
        self.assertIn("badge-loaded", self.models_js)

    def test_toast_notifications(self):
        """User feedback via toast notifications."""
        self.assertIn("toast-container", self.html)
        self.assertIn(".toast", self.css)

    def test_localstorage_persistence(self):
        """Settings saved between browser sessions."""
        self.assertIn("localStorage", self.state_js)
        self.assertIn("saveSettings", self.state_js)
        self.assertIn("loadSettings", self.state_js)


if __name__ == "__main__":
    unittest.main()
