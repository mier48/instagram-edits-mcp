"""JSON-RPC 2.0 message classification and builders."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, TextIO

from ..security.redaction import redact

Kind = Literal["request", "notification", "response", "invalid"]

METHOD_NOT_FOUND = -32601


@dataclass(frozen=True, slots=True)
class Incoming:
    raw: Any
    kind: Kind
    method: str | None = None

    @property
    def id(self) -> Any:
        return self.raw.get("id") if isinstance(self.raw, dict) else None

    @property
    def params(self) -> dict[str, Any]:
        params = self.raw.get("params") if isinstance(self.raw, dict) else None
        return params if isinstance(params, dict) else {}


def classify(raw: Any) -> Incoming:
    if not isinstance(raw, dict):
        return Incoming(raw, "invalid")
    method = raw.get("method")
    if method is None:
        is_reply = "id" in raw and ("result" in raw or "error" in raw)
        return Incoming(raw, "response" if is_reply else "invalid")
    if not isinstance(method, str):
        return Incoming(raw, "invalid")
    return Incoming(raw, "request" if "id" in raw else "notification", method)


def success(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def failure(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": redact(message)}}


def tool_text(rid: Any, text: str, *, is_error: bool = False, data: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": redact(text)}], "isError": is_error}
    if data is not None:
        result["structuredContent"] = data
    return success(rid, result)


def notification(method: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method}


TOOLS_CHANGED = notification("notifications/tools/list_changed")


def read_stream(stream: TextIO, warn: Callable[[str], None], max_line: int) -> Iterator[Any]:
    """Decode newline-delimited JSON, skipping blank, oversized and malformed lines."""
    while line := stream.readline(max_line + 1):
        if len(line) > max_line and not line.endswith("\n"):
            while line and not line.endswith("\n"):
                line = stream.readline(max_line + 1)
            warn(f"skipped an input line longer than {max_line} characters")
            continue
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            warn("skipped an input line that is not JSON")
