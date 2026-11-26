"""The real server process, driven over stdio, against the Edits double."""

from __future__ import annotations

import json
import time

import pytest

from tests.support.edits_double import Behaviour, EditsDouble
from tests.support.stdio import McpProcess, unused_port


@pytest.mark.parametrize("sse", [False, True], ids=["json", "event-stream"])
def test_handshake_listing_and_session_headers(sse: bool) -> None:
    with EditsDouble(Behaviour(reply_as_sse=sse)) as edits:
        mcp = McpProcess(edits.url)
        assert mcp.handshake()["result"]["serverInfo"]["name"] == "edits-double"
        listing = mcp.request("tools/list")["result"]
        assert "resultType" not in listing
        assert [t["name"] for t in listing["tools"]][:2] == ["appState", "projects"]
        mcp.stop()
    after_handshake = edits.record.posts[1:]
    assert after_handshake and all(p["session"] == edits.session_id for p in after_handshake)
    assert all(p["protocol"] == "2025-06-18" for p in after_handshake)


def test_client_is_told_when_opening_a_project_changes_the_tools() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url)
        mcp.handshake()
        mcp.tool_names()
        mcp.tool("appState")
        assert mcp.changes() == 0, "same screen, same tools: no notification"
        mcp.tool("openProject", name="demo")
        mcp.await_message(lambda m: m.get("method") == "notifications/tools/list_changed")
        assert "addText" in mcp.tool_names()
        assert mcp.changes() == 1
        assert not any(str(m.get("id", "")).startswith("instagram-edits-mcp/") for m in mcp.received)
        mcp.stop()
    assert any(str(p["body"].get("id", "")).startswith("instagram-edits-mcp/probe/") for p in edits.record.posts)


def test_tool_tracking_can_be_switched_off() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url, "--no-tool-tracking")
        mcp.handshake()
        mcp.tool("openProject")
        time.sleep(0.3)
        assert mcp.changes() == 0
        mcp.stop()


def test_stream_notifications_are_relayed_when_offered() -> None:
    with EditsDouble(Behaviour(offers_stream=True)) as edits:
        mcp = McpProcess(edits.url, "--no-tool-tracking")
        mcp.handshake()
        time.sleep(0.3)
        mcp.tool("openProject")
        mcp.await_message(lambda m: m.get("method") == "notifications/tools/list_changed")
        mcp.stop()


def test_refused_stream_is_not_retried() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url)
        mcp.handshake()
        time.sleep(2.5)
        assert edits.record.stream_opens == 1
        mcp.stop()


def test_speed_curve_catalog_arrives_retargeted() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url)
        mcp.handshake()
        caps = json.loads(mcp.tool("getCapabilities")["result"]["content"][0]["text"])
        assert {c["kind"]: c["applyTool"] for c in caps["catalogs"]}["speedCurvePreset"] == "setSpeedCurve"
        mcp.stop()


def test_batches_are_relayed_message_by_message() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url)
        mcp.write([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                   {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        mcp.await_message(lambda m: m.get("id") == 1)
        assert "tools" in mcp.await_message(lambda m: m.get("id") == 2)["result"]
        mcp.stop()


def test_noise_on_stdin_is_skipped() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url)
        mcp.write_text('garbage\n\n"string"\n{"id": 5}\n')
        assert mcp.handshake()["result"]
        mcp.stop()


# -- starting before Edits ----------------------------------------------------------------------------


def test_detached_start_then_automatic_attach() -> None:
    port = unused_port()
    mcp = McpProcess(f"http://127.0.0.1:{port}/mcp", INSTAGRAM_EDITS_MCP_RETRY_SECONDS="0.2")
    init = mcp.handshake()["result"]
    assert init["serverInfo"]["name"] == "instagram-edits-mcp" and init["capabilities"]["tools"]["listChanged"]
    assert "not available yet" in init["instructions"]
    assert mcp.tool_names() == ["edits_connection_status"]
    assert mcp.request("ping")["result"] == {}
    assert mcp.tool("appState")["error"]["code"] == -32001
    with EditsDouble(port=port) as edits:
        mcp.await_message(lambda m: m.get("method") == "notifications/tools/list_changed")
        assert {"appState", "openProject"} <= set(mcp.tool_names())
        assert "initialize" in edits.record.methods()
        mcp.stop()


def test_status_tool_attaches_on_demand() -> None:
    port = unused_port()
    mcp = McpProcess(f"http://127.0.0.1:{port}/mcp", INSTAGRAM_EDITS_MCP_RETRY_SECONDS="300")
    mcp.handshake()
    first = mcp.tool("edits_connection_status")["result"]
    assert first["structuredContent"] == {"connected": False}
    assert "Enable Automation Server" in first["content"][0]["text"]
    with EditsDouble(port=port):
        assert mcp.tool("edits_connection_status")["result"]["structuredContent"] == {"connected": True}
        mcp.await_message(lambda m: m.get("method") == "notifications/tools/list_changed")
        assert "edits_connection_status" not in mcp.tool_names()
        mcp.stop()


def test_status_tool_is_local_while_attached() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url)
        mcp.handshake()
        assert mcp.tool("edits_connection_status")["result"]["structuredContent"] == {"connected": True}
        assert not edits.record.calls_to("edits_connection_status")
        mcp.stop()


# -- policy -------------------------------------------------------------------------------------------


def test_read_only_mode() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url, INSTAGRAM_EDITS_MCP_READ_ONLY="1")
        mcp.handshake()
        assert mcp.tool_names() == ["appState", "projects", "getCapabilities"]
        blocked = mcp.tool("openProject", name="demo")["result"]
        assert blocked["isError"] and "read-only" in blocked["content"][0]["text"]
        assert "error" not in mcp.tool("appState")
        assert "tool policy: read-only" in mcp.stop()
    assert not edits.record.calls_to("openProject")


def test_read_only_mode_in_the_editor() -> None:
    with EditsDouble() as edits:
        edits.screen = "editor"
        mcp = McpProcess(edits.url, INSTAGRAM_EDITS_MCP_READ_ONLY="true")
        mcp.handshake()
        names = mcp.tool_names()
        assert "listCatalog" in names and not {"addText", "export"} & set(names)
        mcp.stop()


def test_deny_list() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url, INSTAGRAM_EDITS_MCP_DENY_TOOLS="delete*")
        mcp.handshake()
        assert "deleteProject" not in mcp.tool_names()
        assert mcp.tool("deleteProject", name="x")["result"]["isError"]
        mcp.stop()
    assert not edits.record.calls_to("deleteProject")


def test_methods_outside_the_allow_list_never_reach_edits() -> None:
    with EditsDouble() as edits:
        mcp = McpProcess(edits.url)
        mcp.handshake()
        assert mcp.request("debug/evaluate", {"code": "1"})["error"]["code"] == -32601
        mcp.notify("notifications/custom")
        mcp.request("ping")
        mcp.stop()
    assert not {"debug/evaluate", "notifications/custom"} & set(edits.record.methods())
