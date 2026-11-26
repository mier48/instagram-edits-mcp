from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from instagram_edits_mcp.edits import patches


def reply(result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 7, "result": result}


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"tools": [], "resultType": "complete"}, {"tools": []}),
        ({"tools": [], "cacheScope": "session"}, {"tools": []}),
        ({"tools": [], "cacheScope": "public", "ttlMs": "1m"}, {"tools": []}),
        ({"tools": [], "cacheScope": "public", "ttlMs": True}, {"tools": []}),
        ({"tools": [], "resultType": "complete", "cacheScope": "nope", "ttlMs": 5}, {"tools": []}),
    ],
)
def test_bad_or_incomplete_cache_hints_are_removed(result: dict[str, Any], expected: dict[str, Any]) -> None:
    original = copy.deepcopy(result)
    assert patches.apply(reply(result))["result"] == expected
    assert result == original, "the input is never mutated"


@pytest.mark.parametrize(
    "result",
    [
        {"tools": [], "cacheScope": "private", "ttlMs": 1000},
        {"tools": [], "resultType": "complete", "cacheScope": "public", "ttlMs": 0},
        {"resultType": "task", "taskId": "t-1"},
        {"tools": [], "_meta": {"k": 1}},
    ],
)
def test_valid_results_keep_their_identity(result: dict[str, Any]) -> None:
    message = reply(result)
    assert patches.apply(message) is message


def test_errors_notifications_and_batches_pass_through() -> None:
    for message in ({"id": 1, "error": {"code": 1, "message": "x"}}, {"method": "notifications/x"}, [1, 2], "s"):
        assert patches.apply(message) is message


def test_issues_are_described() -> None:
    issues = patches.cache_hint_issues({"cacheScope": "session"})
    assert any("'session'" in i for i in issues) and any("without ttlMs" in i for i in issues)


def catalog_reply(payload: Any) -> dict[str, Any]:
    return reply({"content": [{"type": "text", "text": json.dumps(payload)}]})


def test_speed_curve_catalog_is_retargeted_in_text_and_structured_content() -> None:
    entry = {"kind": "speedCurvePreset", "applyTool": "selectSpeedPanelPreset", "applyParam": "preset"}
    payload = {"catalogs": [{"kind": "filter", "applyTool": "applyFilter"}, entry]}
    message = catalog_reply(payload)
    message["result"]["structuredContent"] = {"item": dict(entry)}
    fixed = patches.apply(message)["result"]
    catalogs = json.loads(fixed["content"][0]["text"])["catalogs"]
    assert catalogs[1]["applyTool"] == "setSpeedCurve" and catalogs[0]["applyTool"] == "applyFilter"
    assert fixed["structuredContent"]["item"]["applyTool"] == "setSpeedCurve"
    assert "selectSpeedPanelPreset" in message["result"]["content"][0]["text"], "input untouched"


@pytest.mark.parametrize(
    "message",
    [
        catalog_reply({"kind": "filter", "applyTool": "selectSpeedPanelPreset"}),
        reply({"content": [{"type": "text", "text": "selectSpeedPanelPreset is deprecated"}]}),
        catalog_reply({"kind": "speedCurvePreset", "applyTool": "setSpeedCurve"}),
    ],
)
def test_other_mentions_are_left_alone(message: dict[str, Any]) -> None:
    assert patches.apply(message) is message
