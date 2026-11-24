"""Phone and tablet remote: a PIN-protected web page on the local network that sends transport commands.

The request handling is plain functions, so it can be tested without a socket. Commands are
queued and run on the UI thread, never directly from the server thread.
"""

from __future__ import annotations

import hmac
import json
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLOWED_COMMANDS = {"go", "stop", "pause", "panic"}
MAX_FAILED_ATTEMPTS = 10  # after this many wrong PINs, the remote refuses everything until restarted

PAGE = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CueRunner Remote</title>
<style>
body{background:#111;color:#eee;font-family:sans-serif;margin:0;padding:16px}
button{width:100%;font-size:26px;padding:24px;margin:8px 0;border:0;border-radius:8px;color:#fff;font-weight:bold}
#go{background:#2e9e4f}#stop{background:#b03a3a}#pause{background:#c98a1b}#panic{background:#6e1c1c}
#now{font-size:18px;margin:12px 0}#clock{font-family:monospace;font-size:28px}
</style></head><body>
<div id="now">Connecting...</div>
<div id="clock">00:00.0</div>
<button id="go" onclick="cmd('go')">GO</button>
<button id="stop" onclick="cmd('stop')">STOP</button>
<button id="pause" onclick="cmd('pause')">PAUSE</button>
<button id="panic" onclick="cmd('panic')">PANIC</button>
<script>
let pin = sessionStorage.getItem('pin') || prompt('PIN');
sessionStorage.setItem('pin', pin);
async function cmd(c) {
  const r = await fetch('/api/cmd', {method: 'POST',
    headers: {'X-PIN': pin, 'Content-Type': 'application/json'}, body: JSON.stringify({cmd: c})});
  if (r.status === 403) { sessionStorage.removeItem('pin'); location.reload(); }
}
async function poll() {
  try {
    const r = await fetch('/api/status', {headers: {'X-PIN': pin}});
    if (r.ok) {
      const s = await r.json();
      document.getElementById('now').textContent = s.now || 'Standby';
      document.getElementById('clock').textContent = s.track || '00:00.0';
    }
  } catch (e) {}
}
setInterval(poll, 500);
poll();
</script></body></html>
"""


class RemoteState:
    def __init__(self, pin: str) -> None:
        self.pin = pin
        self.commands: queue.Queue[str] = queue.Queue()
        self.status: dict = {}
        self.failed = 0


def _authorised(state: RemoteState, headers) -> bool:
    if state.failed >= MAX_FAILED_ATTEMPTS:
        return False
    given = headers.get("X-PIN", "") or ""
    if hmac.compare_digest(given.encode(), state.pin.encode()):
        return True
    state.failed += 1
    return False


def handle(state: RemoteState, method: str, path: str, headers, body: bytes) -> tuple[int, str, bytes]:
    """Return (status code, content type, response body) for one request."""
    if method == "GET" and path == "/":
        return 200, "text/html; charset=utf-8", PAGE.encode("utf-8")
    if state.failed >= MAX_FAILED_ATTEMPTS:
        return 429, "text/plain", b"Too many wrong PINs. Restart CueRunner to try again."
    if not _authorised(state, headers):
        return 403, "text/plain", b"Wrong PIN"
    if method == "GET" and path == "/api/status":
        return 200, "application/json", json.dumps(state.status).encode("utf-8")
    if method == "POST" and path == "/api/cmd":
        try:
            command = json.loads(body or b"{}").get("cmd")
        except (ValueError, AttributeError):
            return 400, "text/plain", b"Bad request"
        if command not in ALLOWED_COMMANDS:
            return 400, "text/plain", b"Unknown command"
        state.commands.put(command)
        return 200, "application/json", b'{"ok": true}'
    return 404, "text/plain", b"Not found"


def lan_ip() -> str:
    """Best guess at this machine's address on the local network. Nothing is sent."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        probe.close()
        return address
    except OSError:
        return "127.0.0.1"


class RemoteServer:
    def __init__(self, state: RemoteState, port: int) -> None:
        self.state = state

        class Handler(BaseHTTPRequestHandler):
            def _respond(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                code, content_type, payload = handle(state, method, self.path, self.headers, body)
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                self._respond("GET")

            def do_POST(self) -> None:
                self._respond("POST")

            def log_message(self, format, *args) -> None:  # keep the console quiet
                pass

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
