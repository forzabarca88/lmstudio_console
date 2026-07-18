"""JS syntax validation tests.

Catches browser errors before they reach users:
- Syntax errors (via Node.js --check)
- Reserved words used as identifiers (breaks in strict mode ES modules)
"""

import unittest
import os
import re
import subprocess
import shutil
import tempfile

project_root = os.path.dirname(os.path.dirname(__file__))
STATIC_DIR = os.path.join(project_root, "static", "js")

# Reserved words that cause SyntaxError in strict mode (ES modules)
# Used as parameter names or variable names - NOT as default values
STRICT_RESERVED = frozenset([
    "arguments", "await", "break", "case", "catch", "class", "const",
    "continue", "debugger", "default", "delete", "do", "else", "export",
    "extends", "false", "finally", "for", "function", "if", "import",
    "in", "instanceof", "let", "new", "null", "of", "return", "super",
    "switch", "this", "throw", "true", "try", "typeof", "var", "void",
    "while", "with", "yield", "implements", "interface", "package",
    "private", "protected", "public", "static", "async",
])

JS_MODULES = [f for f in os.listdir(STATIC_DIR) if f.endswith(".js")]


def _check_node_available():
    return shutil.which("node") is not None


class TestJSSyntax(unittest.TestCase):
    """All JS modules must parse without errors."""

    @unittest.skipUnless(_check_node_available(), "Node.js not available")
    def test_all_modules_parse(self):
        """ARRANGE: JS module files from static/js/
        ACT: Run node --check on each
        ASSERT: No syntax errors"""
        errors = []
        for name in JS_MODULES:
            filepath = os.path.join(STATIC_DIR, name)
            with open(filepath) as f:
                content = f.read()

            tmpfile = os.path.join(tempfile.gettempdir(), f"check_{name}")
            try:
                with open(tmpfile, "w") as f:
                    f.write(content)
                result = subprocess.run(
                    ["node", "--check", tmpfile],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    errors.append(f"{name}: {result.stderr.strip()}")
            finally:
                if os.path.exists(tmpfile):
                    os.unlink(tmpfile)

        self.assertEqual(errors, [],
                         "Syntax errors:\n" + "\n".join(errors))


class TestReservedWords(unittest.TestCase):
    """No reserved words used as identifiers (breaks strict mode)."""

    def _get_param_name(self, param):
        """Extract parameter name from 'name = default' or 'name: type'."""
        param = param.strip()
        if not param:
            return None
        # Name is before = or :
        name = param.split("=")[0].split(":")[0].strip()
        return name if name else None

    def _check_module(self, name):
        filepath = os.path.join(STATIC_DIR, name)
        with open(filepath) as f:
            content = f.read()

        errors = []

        # Function parameters - only check parameter names, not default values
        for match in re.finditer(
            r'(?:async\s+)?function\s+\w+\s*\(([^\)]*)\)', content
        ):
            params_str = match.group(1)
            for param in params_str.split(","):
                param_name = self._get_param_name(param)
                if param_name and param_name in STRICT_RESERVED:
                    errors.append(f"parameter '{param_name}'")

        # Variable declarations
        for match in re.finditer(r'(?:let|const|var)\s+(\w+)', content):
            if match.group(1) in STRICT_RESERVED:
                errors.append(f"variable '{match.group(1)}'")

        # Import specifiers
        for match in re.finditer(r'import\s+\{([^}]+)\}', content):
            for word in STRICT_RESERVED:
                if re.search(r'\b' + re.escape(word) + r'\b', match.group(1)):
                    errors.append(f"import '{word}'")

        return errors

    def test_no_reserved_identifiers(self):
        """ARRANGE: JS module files
        ACT: Scan for reserved words as identifiers
        ASSERT: None found"""
        errors = []
        for name in JS_MODULES:
            module_errors = self._check_module(name)
            if module_errors:
                errors.append(f"{name}: {', '.join(module_errors)}")

        self.assertEqual(errors, [],
                         "Reserved word violations:\n" + "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
