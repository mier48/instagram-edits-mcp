"""The relay: an MCP server on stdio whose session is backed by Edits' automation server.

It has two states:

* attached: requests are checked (method allow-list, tool policy) and forwarded to Edits; results go
  through `edits.patches`; after each tool call the visible tool set is re-fetched and, if it changed,
  the client is sent `notifications/tools/list_changed`;
* detached: Edits could not be used when the client initialized. The relay completes the handshake
  itself, offers only `edits_connection_status`, and retries in the background. When Edits answers,
  the client's handshake is replayed to it and the client is told to reload its tools.
"""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Callable
from typing import Any, TextIO

from .. import SERVER_NAME
from ..edits import patches
from ..edits.client import EditsConnection
from ..errors import EditsError, EditsImpersonated, EditsUnavailable
from ..security.policy import FORWARDED_NOTIFICATIONS, FORWARDED_REQUESTS, ToolPolicy
from . import status
from .catalog import ToolCatalog
from .messages import (
    METHOD_NOT_FOUND,
    TOOLS_CHANGED,
    Incoming,
    classify,
    failure,
    notification,
    read_stream,
    success,
    tool_text,
)

Log = Callable[[str], None]
MAX_INPUT_LINE = 16 << 20


class Relay:
    def __init__(
        self,
        edits: EditsConnection,
        policy: ToolPolicy,
        output: TextIO,
        log: Log,
        *,
        track_tools: bool = True,
        follow_stream: bool = True,
        retry_seconds: float = 5.0,
    ) -> None:
        self._edits = edits
        self._policy = policy
        self._catalog = ToolCatalog(policy)
        self._output = output
        self._log = log
        self._track_tools = track_tools
        self._follow_stream = follow_stream
        self._retry_seconds = retry_seconds

        self._out_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._attach_lock = threading.Lock()
        self._stop = threading.Event()
        self._detached = False
        self._handshake: dict[str, Any] | None = None
        self._follower: threading.Thread | None = None
        self._probe_ids = itertools.count(1)
        self._handlers: dict[str, Callable[[Incoming], None]] = {
            "initialize": self._on_initialize,
            "ping": self._on_ping,
            "tools/list": self._on_list_tools,
            "tools/call": self._on_call_tool,
        }

    @property
    def url(self) -> str:
        return str(self._edits.endpoint)

    # -- loop -----------------------------------------------------------------------------------------

    def run(self, stream: TextIO) -> int:
        try:
            for payload in read_stream(stream, self._log, MAX_INPUT_LINE):
                for item in payload if isinstance(payload, list) else [payload]:
                    self.handle(item)
        finally:
            self._stop.set()
        return 0

    def handle(self, raw: Any) -> None:
        message = classify(raw)
        if message.kind == "invalid":
            self._log("skipped a message that is not valid JSON-RPC")
        elif message.kind == "response":
            self._forward(message)
        elif message.kind == "notification":
            if message.method not in FORWARDED_NOTIFICATIONS:
                self._log(f"dropped notification {str(message.method)[:80]!r}: not forwarded to Edits")
            elif not self._detached:
                self._forward(message)
        elif message.method not in FORWARDED_REQUESTS:
            self._log(f"refused method {str(message.method)[:80]!r}")
            self._emit(failure(message.id, METHOD_NOT_FOUND, f"{message.method} is not available through {SERVER_NAME}"))
        else:
            self._handlers.get(message.method, self._on_other_request)(message)

    def _emit(self, message: Any) -> None:
        line = json.dumps(patches.apply(message), ensure_ascii=False)
        with self._out_lock:
            self._output.write(line + "\n")
            self._output.flush()

    def _send(self, message: Incoming) -> list[Any] | None:
        """Forward to Edits. On failure, tell the client (for requests) and return None."""
        try:
            return self._edits.send(message.raw)
        except EditsError as err:
            text = err.explain(self.url)
            self._log(text)
            if message.kind == "request":
                self._emit(failure(message.id, err.code, text))
            return None

    def _forward(self, message: Incoming) -> None:
        for reply in self._send(message) or ():
            self._emit(reply)

    # -- request handlers -----------------------------------------------------------------------------

    def _on_other_request(self, message: Incoming) -> None:
        if self._detached:
            self._emit(failure(message.id, EditsUnavailable.code, EditsUnavailable().explain(self.url)))
        else:
            self._forward(message)

    def _on_ping(self, message: Incoming) -> None:
        if self._detached:
            self._emit(success(message.id, {}))
        else:
            self._forward(message)

    def _on_initialize(self, message: Incoming) -> None:
        self._handshake = message.raw
        try:
            replies = self._edits.send(message.raw)
        except (EditsUnavailable, EditsImpersonated) as err:
            self._log(err.explain(self.url))
            self._log("completing the handshake locally until Edits is available")
            self._go_detached()
            self._emit(success(message.id, status.handshake_result(message.params)))
            return
        except EditsError as err:
            self._emit(failure(message.id, err.code, err.explain(self.url)))
            return
        for reply in replies:
            self._emit(reply)
        self._start_follower()

    def _on_list_tools(self, message: Incoming) -> None:
        if self._detached:
            self._emit(success(message.id, {"tools": [status.DEFINITION]}))
            return
        for reply in self._send(message) or ():
            if isinstance(reply, dict) and reply.get("id") == message.id and "result" in reply:
                reply = {**reply, "result": self._catalog.present(reply["result"])}
            self._emit(reply)

    def _on_call_tool(self, message: Incoming) -> None:
        name = message.params.get("name")
        name = name if isinstance(name, str) else ""
        if name == status.NAME:
            self._emit(self._status_report(message.id))
            return
        if self._detached:
            self._emit(failure(message.id, EditsUnavailable.code, EditsUnavailable().explain(self.url)))
            return
        verdict = self._policy.decide(name)
        if not verdict.allowed:
            self._log(f"blocked tool call: {verdict.reason}")
            self._emit(tool_text(message.id, f"Blocked by {SERVER_NAME} policy: {verdict.reason}.", is_error=True))
            return
        replies = self._send(message)
        if replies is None:
            return
        for reply in replies:
            self._emit(reply)
        if self._track_tools and self._tool_set_changed():
            self._log("the visible tool set changed; notifying the client")
            self._emit(TOOLS_CHANGED)

    def _tool_set_changed(self) -> bool:
        probe_id = f"{SERVER_NAME}/probe/{next(self._probe_ids)}"
        try:
            replies = self._edits.send({"jsonrpc": "2.0", "id": probe_id, "method": "tools/list", "params": {}})
        except EditsError as err:
            self._log(f"could not re-check the tool set ({type(err).__name__})")
            return False
        reply = next((r for r in replies if isinstance(r, dict) and r.get("id") == probe_id), None)
        return reply is not None and self._catalog.update(reply.get("result"))

    # -- detached state -------------------------------------------------------------------------------

    def _go_detached(self) -> None:
        with self._state_lock:
            if self._detached:
                return
            self._detached = True
        threading.Thread(target=self._retry_until_attached, name="edits-retry", daemon=True).start()

    def _retry_until_attached(self) -> None:
        while self._detached and not self._stop.wait(self._retry_seconds):
            if self._attach() is None:
                return

    def _attach(self) -> EditsError | None:
        """Replay the client's handshake to Edits. None on success (or when already attached)."""
        with self._attach_lock:
            if not self._detached:
                return None
            handshake = {**(self._handshake or {"jsonrpc": "2.0", "method": "initialize", "params": {}})}
            handshake["id"] = f"{SERVER_NAME}/handshake"
            try:
                self._edits.send(handshake)
                self._edits.send(notification("notifications/initialized"))
            except EditsError as err:
                return err
            with self._state_lock:
                self._detached = False
        self._log("Edits is available now; asking the client to reload its tools")
        self._emit(TOOLS_CHANGED)
        self._start_follower()
        return None

    def _status_report(self, rid: Any) -> dict[str, Any]:
        if not self._detached:
            return tool_text(rid, f"Connected to Instagram Edits at {self.url}.", data={"connected": True})
        problem = self._attach()
        if problem is None:
            return tool_text(
                rid,
                "Instagram Edits just became available and is connected; the client was asked to reload its tools.",
                data={"connected": True},
            )
        return tool_text(
            rid,
            f"{problem.explain(self.url)} Retrying every {self._retry_seconds:g} s; the tools appear on their own.",
            data={"connected": False},
        )

    def _start_follower(self) -> None:
        if not self._follow_stream or self._follower is not None or not self._edits.session.id:
            return
        self._follower = threading.Thread(
            target=self._edits.follow, args=(self._emit, self._stop, self._log), name="edits-stream", daemon=True
        )
        self._follower.start()
