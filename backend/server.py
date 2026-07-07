"""HTTP server that serves static files and proxies API calls to LM Studio."""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import os
import threading
from urllib.parse import urlparse

# Configuration
HOST = os.getenv("LM_CONSOLE_HOST", "0.0.0.0")
PORT = int(os.getenv("LM_CONSOLE_PORT", "8080"))
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


class ConsoleHandler(SimpleHTTPRequestHandler):
    """Serves static files and proxies LM Studio API requests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    # --- Static file serving ---

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Proxy API calls
        if path.startswith("/proxy/"):
            self._proxy_request("GET", path, parsed)
            return

        # Serve static files
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/proxy/"):
            self._proxy_request("POST", path, parsed)
            return

        self.send_error(405, "Method not allowed")

    # --- CORS headers ---

    def _add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Expose-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._add_cors_headers()
        self.end_headers()

    # --- Proxy logic ---

    def _proxy_request(self, method, path, parsed):
        """Forward request to LM Studio and return the response."""
        import urllib.request
        import urllib.error

        # Strip /proxy prefix to get the LM Studio endpoint
        lm_path = path[len("/proxy"):]

        # Build target URL
        target_url = f"{LM_STUDIO_URL}{lm_path}"
        if parsed.query:
            target_url += f"?{parsed.query}"

        # Read request body for POST
        body = None
        if method == "POST":
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                body = self.rfile.read(content_length)

        # Forward to LM Studio
        req = urllib.request.Request(target_url, data=body, method=method)
        req.add_header("Content-Type", "application/json")

        # Forward auth token if present
        auth = self.headers.get("Authorization")
        if auth:
            req.add_header("Authorization", auth)

        try:
            response = urllib.request.urlopen(req)
            status = response.status
            resp_content_type = response.headers.get("Content-Type", "application/json")

            # Check if this is a streaming response (SSE)
            is_stream = "text/event-stream" in resp_content_type

            if is_stream:
                self._proxy_stream(response, status, resp_content_type)
            else:
                self._proxy_buffered(response, status, resp_content_type)

        except urllib.error.HTTPError as e:
            resp_content_type = e.headers.get("Content-Type", "application/json")
            is_stream = "text/event-stream" in resp_content_type

            if is_stream:
                self._proxy_stream(e, e.code, resp_content_type)
            else:
                resp_body = e.read()
                self.send_response(e.code)
                self._add_cors_headers()
                self.send_header("Content-Type", resp_content_type)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)

        except Exception as e:
            error_body = json.dumps({
                "error": str(e),
                "message": f"Failed to connect to LM Studio at {LM_STUDIO_URL}"
            }).encode()
            self.send_response(502)
            self._add_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)

    def _proxy_buffered(self, response, status, content_type):
        """Read entire response and send at once."""
        resp_body = response.read()
        self.send_response(status)
        self._add_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def _proxy_stream(self, response, status, content_type):
        """Stream response in real-time using a background thread."""
        # Send headers first (no Content-Length for streaming)
        self.send_response(status)
        self._add_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Read from upstream in a background thread and write to client
        def stream_copy():
            try:
                while True:
                    chunk = response.read(1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception:
                pass

        t = threading.Thread(target=stream_copy, daemon=True)
        t.start()

    def log_message(self, format, *args):
        """Suppress default logging to keep output clean."""
        pass


def run():
    server = HTTPServer((HOST, PORT), ConsoleHandler)
    print(f"LM Studio Console running at http://{HOST if HOST != '0.0.0.0' else 'localhost'}:{PORT}", flush=True)
    print(f"Proxying to LM Studio at {LM_STUDIO_URL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
        server.server_close()


if __name__ == "__main__":
    run()
