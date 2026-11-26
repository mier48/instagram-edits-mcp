"""Drive the real `instagram-edits-mcp serve` process over stdio."""

from __future__ import annotations

import itertools
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .edits_double import TEST_TOKEN

SRC = str(Path(__file__).resolve().parents[2] / "src")

Predicate = Callable[[dict[str, Any]], bool]


def unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class McpProcess:
    """A server process plus a tiny MCP client: `request()` sends and waits for the matching reply."""

    def __init__(self, url: str, *args: str, token: str | None = TEST_TOKEN, **env: str) -> None:
        environment = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
        environment |= {
            "PYTHONPATH": SRC,
            "INSTAGRAM_EDITS_MCP_URL": url,
            "INSTAGRAM_EDITS_MCP_KEYCHAIN": "off",
            "INSTAGRAM_EDITS_MCP_VERIFY_SERVER": "off",  # the double is not a signed Edits
        }
        if token:
            environment["INSTAGRAM_EDITS_MCP_TOKEN"] = token
        environment |= env
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "instagram_edits_mcp", "serve", *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
        )
        self.received: list[dict[str, Any]] = []
        self._ids = itertools.count(100)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.received.append(json.loads(line))

    def write(self, payload: Any) -> None:
        self.write_text(json.dumps(payload) + "\n")

    def write_text(self, text: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(text)
        self.proc.stdin.flush()

    def await_message(self, predicate: Predicate, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in list(self.received):
                if predicate(message):
                    return message
            time.sleep(0.02)
        raise AssertionError(f"timed out; received so far: {self.received}")

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        rid = next(self._ids)
        self.write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self.await_message(lambda m: m.get("id") == rid, timeout)

    def notify(self, method: str) -> None:
        self.write({"jsonrpc": "2.0", "method": method})

    def handshake(self) -> dict[str, Any]:
        reply = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "tests", "version": "0"}})
        self.notify("notifications/initialized")
        return reply

    def tool(self, tool_name: str, /, timeout: float = 10.0, **arguments: Any) -> dict[str, Any]:
        return self.request("tools/call", {"name": tool_name, "arguments": arguments}, timeout)

    def tool_names(self) -> list[str]:
        return [t["name"] for t in self.request("tools/list")["result"]["tools"]]

    def changes(self) -> int:
        return sum(m.get("method") == "notifications/tools/list_changed" for m in self.received)

    def stop(self) -> str:
        assert self.proc.stdin and self.proc.stderr
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        return self.proc.stderr.read()

    def transcript(self) -> str:
        return json.dumps(self.received)
