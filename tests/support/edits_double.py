"""An in-process test double for the Edits automation server.

It reproduces what matters about the real server: bearer-token auth (403 otherwise), a session id
issued on `initialize`, `tools.listChanged` advertised but never sent, results tagged
`resultType: complete` without caching hints, a tool set that grows when a project is opened, a
speed-curve catalog that names the deprecated tool, and a GET stream refused with 403.

`Behaviour` switches on variations and hostile responses for the security tests.
The token is a fixed test value, not a credential.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TEST_TOKEN = "double-token-for-tests-only-7f3a9c"


def _tool(name: str, **annotations: bool) -> dict[str, Any]:
    tool: dict[str, Any] = {"name": name, "inputSchema": {"type": "object"}}
    if annotations:
        tool["annotations"] = annotations
    return tool


HOME_SCREEN = (
    _tool("appState"),
    _tool("projects"),
    _tool("openProject"),
    _tool("deleteProject", destructiveHint=True),
    _tool("getCapabilities"),
)
EDITOR_SCREEN = (*HOME_SCREEN, _tool("addText"), _tool("listCatalog", readOnlyHint=True), _tool("export"))
CAPABILITIES = {
    "schemaVersion": "test-schema-1",
    "catalogs": [
        {"kind": "filter", "applyTool": "applyFilter"},
        {"kind": "speedCurvePreset", "applyTool": "selectSpeedPanelPreset", "applyParam": "preset"},
    ],
}


@dataclass
class Behaviour:
    reply_as_sse: bool = False
    offers_stream: bool = False
    cache_scope: str | None = None
    redirect: tuple[int, str] | None = None  # (status, Location)
    echo_auth_with_status: int | None = None
    oversized_body: int | None = None


@dataclass
class Record:
    """What the double saw."""

    posts: list[dict[str, Any]] = field(default_factory=list)
    authorizations: list[str | None] = field(default_factory=list)
    stream_opens: int = 0

    def calls_to(self, tool: str) -> list[dict[str, Any]]:
        return [p for p in self.posts if p["body"].get("method") == "tools/call" and p["body"]["params"]["name"] == tool]

    def methods(self) -> list[str | None]:
        return [p["body"].get("method") for p in self.posts]


class EditsDouble:
    def __init__(self, behaviour: Behaviour | None = None, port: int = 0) -> None:
        self.behaviour = behaviour or Behaviour()
        self.record = Record()
        self.screen = "home"
        self.session_id = "double-session-1"
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler_class())
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> EditsDouble:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- protocol -------------------------------------------------------------------------------------

    def respond(self, body: dict[str, Any]) -> dict[str, Any] | None:
        method, rid = body.get("method"), body.get("id")
        if rid is None:
            return None
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "edits-double", "version": "1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": list(EDITOR_SCREEN if self.screen == "editor" else HOME_SCREEN), "resultType": "complete"}
            if self.behaviour.cache_scope is not None:
                result["cacheScope"] = self.behaviour.cache_scope
        elif method == "tools/call":
            result = self._call(body["params"]["name"])
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _call(self, name: str) -> dict[str, Any]:
        if name == "openProject":
            self.screen = "editor"
            if self.behaviour.offers_stream:
                self._events.put({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        payload: Any = CAPABILITIES if name == "getCapabilities" else {"ok": True, "screen": self.screen}
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}

    # -- HTTP -----------------------------------------------------------------------------------------

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        double = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def reply(self, status: int, body: bytes = b"", ctype: str = "application/json", **extra: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for name, value in extra.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def gate(self) -> bool:
                auth = self.headers.get("Authorization")
                double.record.authorizations.append(auth)
                b = double.behaviour
                if b.redirect:
                    self.reply(b.redirect[0], Location=b.redirect[1])
                elif b.echo_auth_with_status:
                    self.reply(b.echo_auth_with_status, json.dumps({"received": auth}).encode())
                elif auth != f"Bearer {TEST_TOKEN}":
                    self.reply(403, b'{"error": "access token required"}')
                else:
                    return True
                return False

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if not self.gate():
                    return
                body = json.loads(raw)
                double.record.posts.append(
                    {"body": body, "session": self.headers.get("Mcp-Session-Id"),
                     "protocol": self.headers.get("MCP-Protocol-Version")}
                )
                if double.behaviour.oversized_body:
                    self.reply(200, b'"' + b"x" * double.behaviour.oversized_body + b'"')
                    return
                answer = double.respond(body)
                if answer is None:
                    self.reply(202)
                    return
                extra = {"Mcp-Session-Id": double.session_id} if body.get("method") == "initialize" else {}
                if double.behaviour.reply_as_sse:
                    self.reply(200, f"event: message\ndata: {json.dumps(answer)}\n\n".encode(), "text/event-stream", **extra)
                else:
                    self.reply(200, json.dumps(answer).encode(), **extra)

            def do_GET(self) -> None:
                if not self.gate():
                    return
                double.record.stream_opens += 1
                if not double.behaviour.offers_stream:
                    self.reply(403, b'{"error": "forbidden"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                while True:
                    try:
                        event = double._events.get(timeout=5)
                    except queue.Empty:
                        return
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()

        return Handler


class Sink:
    """A loopback listener that records connections: a stand-in for a proxy or a redirect target."""

    def __init__(self) -> None:
        self.hits = 0
        self.received = b""
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def handle(self) -> None:
                sink.hits += 1
                try:
                    sink.received += self.request.recv(65536)
                except OSError:
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> Sink:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
