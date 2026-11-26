"""Which JSON-RPC methods and which Edits tools the client may use.

A tool is permitted when all of these hold:
- it matches no `deny` pattern;
- `allow` is empty, or it matches an `allow` pattern;
- read-only mode is off, or the tool is read-only.

A tool counts as read-only when Edits annotates it `readOnlyHint: true`, or, if Edits says nothing
either way, when its name reads as a query (`getX`, `listX`, `xStatus`, a known lookup). A tool
annotated `readOnlyHint: false` or `destructiveHint: true` is never read-only.

The policy is applied twice: denied tools are dropped from `tools/list`, and calls to them are
refused even if the client never saw them listed.
"""

from __future__ import annotations

import fnmatch
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..config import Config

# Requests the bridge forwards to Edits. Anything else is answered locally with "method not found".
FORWARDED_REQUESTS = frozenset(
    {
        "initialize",
        "ping",
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/templates/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
        "completion/complete",
        "logging/setLevel",
    }
)
FORWARDED_NOTIFICATIONS = frozenset(
    {
        "notifications/initialized",
        "notifications/cancelled",
        "notifications/progress",
        "notifications/roots/list_changed",
    }
)

_QUERY_VERBS = ("get", "list", "search", "find", "describe", "inspect", "query", "read", "count", "capture")
_QUERY_NAME = re.compile(rf"^(?:{'|'.join(_QUERY_VERBS)})(?:[A-Z0-9_].*)?$")
# Edits lookups whose names do not read as queries.
KNOWN_READ_ONLY = frozenset(
    {
        "appState",
        "projects",
        "elements",
        "beatMarkers",
        "audioOnsets",
        "previewVideoStatus",
        "previewVideoFrames",
        "exportStatus",
        "edits_connection_status",
    }
)


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str = ""


def _matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def looks_read_only(name: str, annotations: Any) -> bool:
    hints = annotations if isinstance(annotations, dict) else {}
    if hints.get("destructiveHint") is True or hints.get("readOnlyHint") is False:
        return False
    if hints.get("readOnlyHint") is True:
        return True
    return name in KNOWN_READ_ONLY or bool(_QUERY_NAME.match(name)) or name.endswith("Status")


class ToolPolicy:
    def __init__(
        self, read_only: bool = False, allow: Iterable[str] = (), deny: Iterable[str] = ()
    ) -> None:
        self.read_only = read_only
        self.allow = tuple(allow)
        self.deny = tuple(deny)
        self._annotations: dict[str, Any] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: Config) -> ToolPolicy:
        return cls(config.read_only, config.allow, config.deny)

    @property
    def unrestricted(self) -> bool:
        return not (self.read_only or self.allow or self.deny)

    def describe(self) -> str:
        parts = ["read-only" if self.read_only else "full access"]
        if self.allow:
            parts.append(f"allow={','.join(self.allow)}")
        if self.deny:
            parts.append(f"deny={','.join(self.deny)}")
        return "; ".join(parts)

    def decide(self, name: str, annotations: Any = None) -> Decision:
        if annotations is None:
            with self._lock:
                annotations = self._annotations.get(name)
        if _matches(name, self.deny):
            return Decision(False, f"the tool '{name}' is blocked by the deny list")
        if self.allow and not _matches(name, self.allow):
            return Decision(False, f"the tool '{name}' is not on the allow list")
        if self.read_only and not looks_read_only(name, annotations):
            return Decision(False, f"the bridge is in read-only mode and '{name}' may change the project")
        return Decision(True)

    def filter_tools_result(self, result: Any) -> Any:
        """Drop denied tools from a tools/list result (a copy; the input is not modified)."""
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return result
        kept = []
        with self._lock:
            for tool in result["tools"]:
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                    continue
                self._annotations[tool["name"]] = tool.get("annotations")
        for tool in result["tools"]:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                if self.decide(tool["name"], tool.get("annotations")).allowed:
                    kept.append(tool)
        if len(kept) == len(result["tools"]):
            return result
        return dict(result, tools=kept)
