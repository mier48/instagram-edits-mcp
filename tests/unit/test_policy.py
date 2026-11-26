from __future__ import annotations

import pytest

from instagram_edits_mcp.security.policy import (
    FORWARDED_NOTIFICATIONS,
    FORWARDED_REQUESTS,
    ToolPolicy,
    looks_read_only,
)

QUERIES = ["appState", "projects", "elements", "getTransform", "listCatalog", "captureFrame", "exportStatus"]
CHANGES = ["addText", "openProject", "deleteProject", "export", "setSpeedCurve", "getaway", "listen", "newProject"]


@pytest.mark.parametrize("name", QUERIES)
def test_query_names_count_as_read_only(name: str) -> None:
    assert looks_read_only(name, None)


@pytest.mark.parametrize("name", CHANGES)
def test_other_names_do_not(name: str) -> None:
    assert not looks_read_only(name, None)


@pytest.mark.parametrize(
    ("name", "hints", "expected"),
    [
        ("renderThumb", {"readOnlyHint": True}, True),
        ("getX", {"readOnlyHint": False}, False),
        ("getX", {"destructiveHint": True}, False),
        ("y", {"readOnlyHint": True, "destructiveHint": True}, False),
    ],
)
def test_annotations_take_precedence(name: str, hints: dict[str, bool], expected: bool) -> None:
    assert looks_read_only(name, hints) is expected


def test_no_restrictions_by_default() -> None:
    policy = ToolPolicy()
    assert policy.unrestricted and policy.decide("deleteProject").allowed


def test_deny_overrides_allow() -> None:
    policy = ToolPolicy(allow=["*Project"], deny=["delete*"])
    assert policy.decide("openProject").allowed
    assert not policy.decide("deleteProject").allowed
    assert not policy.decide("addText").allowed


def test_read_only_cannot_be_lifted_by_allow() -> None:
    policy = ToolPolicy(read_only=True, allow=["addText", "appState"])
    assert policy.decide("appState").allowed
    verdict = policy.decide("addText")
    assert not verdict.allowed and "read-only" in verdict.reason


def test_listing_is_filtered_and_annotations_are_remembered() -> None:
    policy = ToolPolicy(read_only=True)
    listing = {"tools": [{"name": "appState"}, {"name": "renderThumb", "annotations": {"readOnlyHint": True}},
                         {"name": "addText"}, "garbage"], "resultType": "complete"}
    shown = policy.filter_tools_result(listing)
    assert [t["name"] for t in shown["tools"]] == ["appState", "renderThumb"]
    assert len(listing["tools"]) == 4
    assert policy.decide("renderThumb").allowed, "a later call is judged with the listed annotations"


def test_unrestricted_policy_returns_the_listing_untouched() -> None:
    listing = {"tools": [{"name": "x"}]}
    assert ToolPolicy().filter_tools_result(listing) is listing


def test_forwarded_methods_are_a_closed_set() -> None:
    assert {"initialize", "tools/list", "tools/call", "ping"} <= FORWARDED_REQUESTS
    assert "sampling/createMessage" not in FORWARDED_REQUESTS
    assert "notifications/initialized" in FORWARDED_NOTIFICATIONS
