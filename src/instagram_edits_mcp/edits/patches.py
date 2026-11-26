"""Small, targeted corrections applied to what Edits sends before the client sees it.

Each patch is a pure function `message -> message` that returns its input object unchanged (same
identity) when it has nothing to do, and a modified copy otherwise; inputs are never mutated.

* `cache_hints`: Edits tags results `"resultType": "complete"` without the `ttlMs` / `cacheScope` pair
  that newer MCP schemas expect with it, and some client versions then reject the whole result. The
  tag is removed unless a valid pair is present; malformed hints are removed too.
* `speed_curve_catalog`: Edits' catalogs name `selectSpeedPanelPreset` as the tool that applies speed
  curve presets, while that tool describes itself as deprecated; `setSpeedCurve` accepts the same
  `preset` value. Catalog entries are pointed at the working tool.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

Patch = Callable[[Any], Any]

CACHE_SCOPES = frozenset({"public", "private"})
SPEED_KIND = "speedCurvePreset"
DEPRECATED_SPEED_TOOL = "selectSpeedPanelPreset"
SPEED_TOOL = "setSpeedCurve"


def cache_hint_issues(result: Any) -> list[str]:
    """Human-readable problems with a result's caching hints (empty when there are none)."""
    if not isinstance(result, dict):
        return []
    issues = []
    scope, ttl = result.get("cacheScope"), result.get("ttlMs")
    if "cacheScope" in result and scope not in CACHE_SCOPES:
        issues.append(f"cacheScope={scope!r} is not 'public' or 'private'")
    if "ttlMs" in result and (isinstance(ttl, bool) or not isinstance(ttl, int | float)):
        issues.append(f"ttlMs={ttl!r} is not a number")
    if "cacheScope" in result and "ttlMs" not in result:
        issues.append("cacheScope is set without ttlMs")
    return issues


def _has_valid_hints(result: dict[str, Any]) -> bool:
    return "cacheScope" in result and "ttlMs" in result and not cache_hint_issues(result)


def cache_hints(message: Any) -> Any:
    result = message.get("result") if isinstance(message, dict) else None
    if not isinstance(result, dict):
        return message
    drop: set[str] = set()
    if cache_hint_issues(result):
        drop |= {"cacheScope", "ttlMs"}
    if result.get("resultType") == "complete" and not _has_valid_hints(result):
        drop.add("resultType")
    if not drop & result.keys():
        return message
    return {**message, "result": {k: v for k, v in result.items() if k not in drop}}


def _retarget(node: Any) -> Any:
    """Return `node` itself if no speed catalog entry inside it needs fixing, else a fixed copy."""
    if isinstance(node, list):
        items = [_retarget(item) for item in node]
        return node if all(a is b for a, b in zip(items, node, strict=True)) else items
    if not isinstance(node, dict):
        return node
    copy = {key: _retarget(value) for key, value in node.items()}
    changed = any(copy[key] is not node[key] for key in node)
    if copy.get("kind") == SPEED_KIND and copy.get("applyTool") == DEPRECATED_SPEED_TOOL:
        copy["applyTool"] = SPEED_TOOL
        changed = True
    return copy if changed else node


def speed_curve_catalog(message: Any) -> Any:
    result = message.get("result") if isinstance(message, dict) else None
    if not isinstance(result, dict):
        return message
    patched = dict(result)
    if "structuredContent" in result:
        patched["structuredContent"] = _retarget(result["structuredContent"])
    content = result.get("content")
    if isinstance(content, list):
        patched["content"] = [_retarget_text_block(block) for block in content]
        if all(a is b for a, b in zip(patched["content"], content, strict=True)):
            patched["content"] = content
    if all(patched[k] is result[k] for k in result):
        return message
    return {**message, "result": patched}


def _retarget_text_block(block: Any) -> Any:
    text = block.get("text") if isinstance(block, dict) else None
    if not isinstance(text, str) or DEPRECATED_SPEED_TOOL not in text:
        return block
    try:
        data = json.loads(text)
    except ValueError:
        return block
    fixed = _retarget(data)
    if fixed is data:
        return block
    return {**block, "text": json.dumps(fixed, indent=2, ensure_ascii=False)}


PATCHES: tuple[Patch, ...] = (speed_curve_catalog, cache_hints)


def apply(message: Any) -> Any:
    for patch in PATCHES:
        message = patch(message)
    return message
