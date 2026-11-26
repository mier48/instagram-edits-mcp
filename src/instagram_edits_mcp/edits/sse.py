"""Incremental decoder for `text/event-stream` bodies whose events carry JSON-RPC payloads."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from ..errors import EditsBadReply


class EventDecoder:
    """Feed it lines; it yields the JSON value of each complete event. Comments and other fields are ignored."""

    def __init__(self, max_event_bytes: int) -> None:
        self._limit = max_event_bytes
        self._buffer: list[str] = []
        self._size = 0

    def feed(self, line: str) -> Iterator[Any]:
        line = line.rstrip("\r\n")
        if line == "":
            yield from self._dispatch()
            return
        if line.startswith(":"):
            return
        name, sep, value = line.partition(":")
        if name != "data":
            return
        if sep and value.startswith(" "):
            value = value[1:]
        self._size += len(value) + 1
        if self._size > self._limit:
            raise EditsBadReply("an event in the stream is larger than the configured limit")
        self._buffer.append(value)

    def finish(self) -> Iterator[Any]:
        yield from self._dispatch()

    def _dispatch(self) -> Iterator[Any]:
        if not self._buffer:
            return
        text = "\n".join(self._buffer)
        self._buffer, self._size = [], 0
        yield parse_json(text)


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        raise EditsBadReply("the reply is not valid JSON") from None


def decode(lines: Iterable[str], max_event_bytes: int) -> Iterator[Any]:
    decoder = EventDecoder(max_event_bytes)
    for line in lines:
        yield from decoder.feed(line)
    yield from decoder.finish()


def as_messages(payload: Any) -> list[Any]:
    """A JSON-RPC payload is a single message or a batch."""
    return payload if isinstance(payload, list) else [payload]
