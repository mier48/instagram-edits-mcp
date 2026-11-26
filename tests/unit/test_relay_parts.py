"""SSE decoding, message classification, input reading and the tool catalog."""

from __future__ import annotations

import io

import pytest

from instagram_edits_mcp.edits.sse import EventDecoder, decode
from instagram_edits_mcp.errors import EditsBadReply
from instagram_edits_mcp.relay.catalog import ToolCatalog, fingerprint
from instagram_edits_mcp.relay.messages import classify, read_stream
from instagram_edits_mcp.security.policy import ToolPolicy


def test_sse_events_are_joined_and_comments_skipped() -> None:
    lines = [": keep-alive", "event: message", 'data: {"a":', "data: 1}", "", 'data:{"b": 2}', ""]
    assert list(decode(lines, 1000)) == [{"a": 1}, {"b": 2}]


def test_sse_trailing_event_without_blank_line() -> None:
    assert list(decode(['data: {"c": 3}'], 1000)) == [{"c": 3}]


def test_sse_limits_and_bad_json() -> None:
    with pytest.raises(EditsBadReply):
        list(EventDecoder(10).feed("data: " + "x" * 20))
    with pytest.raises(EditsBadReply):
        list(decode(["data: {not json", ""], 1000))


@pytest.mark.parametrize(
    ("raw", "kind", "method"),
    [
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "request", "tools/list"),
        ({"jsonrpc": "2.0", "method": "notifications/initialized"}, "notification", "notifications/initialized"),
        ({"jsonrpc": "2.0", "id": 1, "result": {}}, "response", None),
        ({"jsonrpc": "2.0", "id": 1}, "invalid", None),
        ({"jsonrpc": "2.0", "id": 1, "method": 3}, "invalid", None),
        ("text", "invalid", None),
    ],
)
def test_classification(raw: object, kind: str, method: str | None) -> None:
    message = classify(raw)
    assert (message.kind, message.method) == (kind, method)


def test_reader_skips_noise_and_overlong_lines() -> None:
    warnings: list[str] = []
    stream = io.StringIO("\n" + "nope\n" + "[" + "1," * 40 + "1]\n" + '{"ok": 1}\n')
    assert list(read_stream(stream, warnings.append, max_line=50)) == [{"ok": 1}]
    assert any("longer than" in w for w in warnings) and any("not JSON" in w for w in warnings)


def listing(*names: str) -> dict[str, object]:
    return {"tools": [{"name": n} for n in names]}


def test_fingerprint_ignores_order_and_partial_pages() -> None:
    assert fingerprint(listing("a", "b")) == fingerprint(listing("b", "a"))
    assert fingerprint({**listing("a"), "nextCursor": "2"}) is None
    assert fingerprint({"tools": "x"}) is None


def test_catalog_reports_changes_only_after_a_baseline() -> None:
    catalog = ToolCatalog(ToolPolicy())
    assert catalog.update(listing("a")) is False, "the first sighting is a baseline"
    assert catalog.update(listing("a")) is False
    assert catalog.update(listing("a", "b")) is True


def test_catalog_ignores_changes_hidden_by_the_policy() -> None:
    catalog = ToolCatalog(ToolPolicy(read_only=True))
    catalog.present(listing("appState"))
    assert catalog.update(listing("appState", "addText")) is False
    assert catalog.update(listing("appState", "addText", "listCatalog")) is True
