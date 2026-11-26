"""The tool set the client is allowed to see, and when it changes.

Edits replaces its tools as the user moves between screens but does not notify clients, although it
advertises `tools.listChanged`. The catalog keeps a fingerprint of the visible (post-policy) tool
names so the relay can tell the client exactly when a re-list is worth it.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from ..security.policy import ToolPolicy


def fingerprint(result: Any) -> str | None:
    """A digest of the tool names in a complete tools/list result, or None for partial or invalid ones."""
    if not isinstance(result, dict) or result.get("nextCursor") or not isinstance(result.get("tools"), list):
        return None
    names = sorted({t["name"] for t in result["tools"] if isinstance(t, dict) and isinstance(t.get("name"), str)})
    return hashlib.sha256("\0".join(names).encode()).hexdigest()


class ToolCatalog:
    def __init__(self, policy: ToolPolicy) -> None:
        self.policy = policy
        self._current: str | None = None
        self._lock = threading.Lock()

    def present(self, result: Any) -> Any:
        """Filter a tools/list result for the client and remember what it was shown."""
        visible = self.policy.filter_tools_result(result)
        if (digest := fingerprint(visible)) is not None:
            with self._lock:
                self._current = digest
        return visible

    def update(self, result: Any) -> bool:
        """Record a freshly fetched tools/list result; True when the visible set differs from the last one shown."""
        digest = fingerprint(self.policy.filter_tools_result(result))
        if digest is None:
            return False
        with self._lock:
            previous, self._current = self._current, digest
        return previous is not None and previous != digest
