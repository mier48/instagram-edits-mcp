"""Keep secrets out of every diagnostic: logs, JSON-RPC error messages, doctor output and crash reports.

All text that leaves the process for a human or a model (stderr, error messages) goes through
`redact()`. Registered secrets are replaced by exact match; common credential shapes are masked
even when they were never registered (a token echoed back by a misbehaving server, for example).
"""

from __future__ import annotations

import re
import sys
import threading
from typing import TextIO

MASK = "<redacted>"

# Shapes that look like credentials, masked even when the value was never registered. Values shorter
# than 8 characters are left alone so ordinary prose ("token: none") stays readable.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=:-]{8,}"),
    re.compile(r"(?i)\b(authorization|x-api-key|api[_-]?key|access[_-]?token|token|secret|password)"
               r"(\"?\s*[:=]\s*\"?)[^\s\"',;&}]{8,}"),
)

_MIN_SECRET_LENGTH = 4


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: str | None) -> None:
        if secret and len(secret) >= _MIN_SECRET_LENGTH:
            with self._lock:
                self._secrets.add(secret)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def redact(self, text: object) -> str:
        out = str(text)
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            out = out.replace(secret, MASK)
        out = _PATTERNS[0].sub(lambda m: f"{m.group(1)} {MASK}", out)
        out = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
        return out


REDACTOR = Redactor()


def redact(text: object) -> str:
    return REDACTOR.redact(text)


class Logger:
    """Writes redacted, prefixed lines to stderr. stdout belongs to the MCP protocol."""

    def __init__(self, stream: TextIO | None = None, prefix: str = "[instagram-edits-mcp]") -> None:
        self._stream = stream
        self._prefix = prefix
        self._lock = threading.Lock()

    def __call__(self, message: object) -> None:
        stream = self._stream or sys.stderr
        line = f"{self._prefix} {redact(message)}\n"
        with self._lock:
            stream.write(line)
            stream.flush()
