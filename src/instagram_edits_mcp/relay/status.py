"""The one tool the relay serves itself: available while Edits is not, so the client has something to call."""

from __future__ import annotations

from typing import Any

from .. import SERVER_NAME, __version__

NAME = "edits_connection_status"

DEFINITION: dict[str, Any] = {
    "name": NAME,
    "title": "Instagram Edits connection",
    "description": (
        "Reports whether Instagram Edits for Mac can be reached and, if it can, connects to it right away. "
        "Only offered while Edits is unavailable; once connected, Edits' own tools take its place."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}

DETACHED_INSTRUCTIONS = (
    "Instagram Edits for Mac is not available yet, so its tools are not listed. Ask the user to open Edits "
    f"and enable Settings > Automation > Enable Automation Server, then call {NAME}. The tools also appear "
    "automatically as soon as Edits answers."
)


def handshake_result(client_params: dict[str, Any]) -> dict[str, Any]:
    """What the relay answers to `initialize` on its own while Edits is unavailable."""
    version = client_params.get("protocolVersion")
    return {
        "protocolVersion": version if isinstance(version, str) else "2025-06-18",
        "capabilities": {"tools": {"listChanged": True}},
        "serverInfo": {"name": SERVER_NAME, "version": __version__},
        "instructions": DETACHED_INSTRUCTIONS,
    }
