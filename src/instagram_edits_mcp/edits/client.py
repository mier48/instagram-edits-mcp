"""MCP Streamable HTTP client for the Edits automation server.

Built directly on `http.client` (not `urllib.request`, which follows redirects with the Authorization
header and honours proxy settings). By construction:

* no proxy: neither environment variables nor macOS system proxies are consulted;
* no redirects: any 3xx status is an error, so the token can never be forwarded to another URL;
* no other scheme or host: `Endpoint` only admits a loopback IP, and the socket's peer is re-checked;
* no request leaves before `ServerVerifier` confirms that the listener is a signed Edits;
* replies and stream events are size-limited.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..config import Endpoint
from ..errors import EditsBadReply, EditsError, EditsImpersonated, EditsRejected, EditsUnavailable
from ..security.identity import NoListener, ServerNotTrusted, ServerVerifier
from ..security.keychain import Token
from .sse import EventDecoder, as_messages, decode, parse_json

USER_AGENT = "instagram-edits-mcp"
STREAM_UNSUPPORTED = frozenset({403, 404, 405})  # Edits answers 403 to the GET stream; the spec says 405
MAX_STREAM_LINE = 1 << 20
JSON_AND_SSE = "application/json, text/event-stream"


@dataclass
class Session:
    """What Edits hands out during the handshake and expects back on every later request."""

    id: str | None = None
    protocol: str | None = None


def _header_value(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= 256 and value.isascii() and value.isprintable():
        return value
    return None


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


class EditsConnection:
    def __init__(
        self,
        endpoint: Endpoint,
        token: Token | None,
        *,
        verifier: ServerVerifier | None = None,
        timeout: float = 600.0,
        max_reply_bytes: int = 64 << 20,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_reply_bytes = max_reply_bytes
        self.session = Session()
        self._token = token
        self._verifier = verifier
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"EditsConnection({self.endpoint}, token={'<redacted>' if self._token else 'none'})"

    # -- low level ------------------------------------------------------------------------------------

    def _open(self, timeout: float | None) -> http.client.HTTPConnection:
        if self._verifier is not None:  # also tokenless: an impostor could still feed fake tools to the model
            try:
                self._verifier.check(self.endpoint.port)
            except NoListener as err:
                raise EditsUnavailable(str(err)) from None
            except ServerNotTrusted as err:
                raise EditsImpersonated(str(err)) from None
        conn = http.client.HTTPConnection(self.endpoint.host, self.endpoint.port, timeout=timeout)
        try:
            conn.connect()
            peer = conn.sock.getpeername()[0] if conn.sock else ""
        except OSError as err:
            conn.close()
            raise EditsUnavailable(type(err).__name__) from None
        if not _loopback(peer):
            conn.close()
            raise EditsImpersonated("the connection did not end at a loopback address; nothing was sent.")
        return conn

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": USER_AGENT}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.reveal()}"
        with self._lock:
            if self.session.id:
                headers["Mcp-Session-Id"] = self.session.id
            if self.session.protocol:
                headers["MCP-Protocol-Version"] = self.session.protocol
        return headers

    def _exchange(
        self, conn: http.client.HTTPConnection, method: str, body: bytes | None, headers: dict[str, str]
    ) -> http.client.HTTPResponse:
        try:
            conn.request(method, self.endpoint.path, body=body, headers=headers)
            return conn.getresponse()
        except OSError as err:
            raise EditsUnavailable(type(err).__name__) from None
        except http.client.HTTPException as err:
            raise EditsBadReply(f"malformed HTTP reply ({type(err).__name__})") from None

    def _body(self, response: http.client.HTTPResponse) -> bytes:
        length = response.getheader("Content-Length", "")
        if length.isdigit() and int(length) > self.max_reply_bytes:
            raise EditsBadReply("the reply is larger than the configured limit")
        try:
            data = response.read(self.max_reply_bytes + 1)
        except (OSError, http.client.HTTPException):
            raise EditsUnavailable("the connection closed during the reply") from None
        if len(data) > self.max_reply_bytes:
            raise EditsBadReply("the reply is larger than the configured limit")
        return data

    # -- requests -------------------------------------------------------------------------------------

    def send(self, message: Any) -> list[Any]:
        """POST one JSON-RPC message or batch; return every message Edits answers with."""
        headers = self._headers(JSON_AND_SSE) | {"Content-Type": "application/json"}
        conn = self._open(self.timeout)
        try:
            response = self._exchange(conn, "POST", json.dumps(message).encode(), headers)
            if response.status >= 300:
                raise EditsRejected(response.status)
            if session_id := _header_value(response.getheader("Mcp-Session-Id")):
                with self._lock:
                    self.session.id = session_id
            kind = (response.getheader("Content-Type") or "").lower()
            text = self._body(response).decode("utf-8", "replace")
        finally:
            conn.close()
        if not text.strip():
            return []
        if "text/event-stream" in kind:
            replies = [m for payload in decode(text.splitlines(), self.max_reply_bytes) for m in as_messages(payload)]
        else:
            replies = as_messages(parse_json(text))
        self._learn_protocol(replies)
        return replies

    def _learn_protocol(self, replies: list[Any]) -> None:
        for reply in replies:
            result = reply.get("result") if isinstance(reply, dict) else None
            if isinstance(result, dict) and "serverInfo" in result:
                if version := _header_value(result.get("protocolVersion")):
                    with self._lock:
                        self.session.protocol = version

    def offers_stream(self) -> bool | None:
        """True if Edits serves the GET notification stream, False if it refuses it, None when unknown."""
        try:
            conn = self._open(3.0)
        except EditsError:
            return None
        try:
            status = self._exchange(conn, "GET", None, self._headers("text/event-stream")).status
        except EditsError:
            return None
        finally:
            conn.close()
        return True if status == 200 else False if status in STREAM_UNSUPPORTED else None

    def follow(self, deliver: Callable[[Any], None], stop: threading.Event, log: Callable[[str], None]) -> None:
        """Deliver server-initiated messages until `stop` is set, reconnecting with exponential backoff."""
        backoff = 1.0
        while not stop.is_set():
            try:
                for message in self._stream_once():
                    backoff = 1.0
                    deliver(message)
                    if stop.is_set():
                        return
            except _StreamRefused as refused:
                log(f"Edits offers no notification stream (HTTP {refused.status}); relying on tool-list checks")
                return
            except EditsImpersonated as err:
                log(f"notification stream abandoned: {err}")
                return
            except Exception as err:
                if stop.is_set():
                    return
                log(f"notification stream interrupted ({type(err).__name__}); reconnecting in {backoff:g} s")
            stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)

    def _stream_once(self) -> Iterator[Any]:
        conn = self._open(None)
        try:
            response = self._exchange(conn, "GET", None, self._headers("text/event-stream"))
            if response.status in STREAM_UNSUPPORTED:
                raise _StreamRefused(response.status)
            if response.status != 200:
                raise EditsRejected(response.status)
            decoder = EventDecoder(16 * MAX_STREAM_LINE)
            while raw := response.readline(MAX_STREAM_LINE + 1):
                if len(raw) > MAX_STREAM_LINE:
                    raise EditsBadReply("a stream line is too long")
                for payload in decoder.feed(raw.decode("utf-8", "replace")):
                    yield from as_messages(payload)
        finally:
            conn.close()


class _StreamRefused(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
