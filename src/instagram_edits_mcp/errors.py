"""Exception hierarchy. Every message is safe to show: none carries a secret or a response body."""

from __future__ import annotations

from typing import ClassVar


class ConfigError(ValueError):
    """The configuration is invalid or unsafe."""


class CredentialError(RuntimeError):
    """The access token could not be stored or read."""


class EditsError(Exception):
    """Talking to Edits failed. `code` is the JSON-RPC error code reported to the client."""

    code: ClassVar[int] = -32000

    def explain(self, url: str) -> str:
        return f"Edits could not be used: {self}"


class EditsUnavailable(EditsError):
    """Nothing usable answers at the configured address."""

    code = -32001

    def explain(self, url: str) -> str:
        return f"Cannot reach Edits at {url}. Open Edits and enable Settings > Automation > Enable Automation Server."


class EditsRejected(EditsError):
    """Edits answered with an HTTP error status (redirects included: they are never followed)."""

    code = -32002

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)

    def explain(self, url: str) -> str:
        if self.is_auth:
            return (
                f"Edits refused the access token (HTTP {self.status}). Copy the current token from "
                "Edits > Settings > Automation and update it (/plugin configure in Claude Code, "
                "or `instagram-edits-mcp set-token`)."
            )
        if 300 <= self.status < 400:
            return f"Edits answered with a redirect (HTTP {self.status}); redirects are never followed."
        return f"Edits answered HTTP {self.status}."


class EditsImpersonated(EditsError):
    """The process behind the Edits address is not a correctly signed Edits. Nothing was sent to it."""

    code = -32003

    def explain(self, url: str) -> str:
        return (
            f"{self} Another program is using the Edits address, so the request was not sent. "
            "Quit that program or run `instagram-edits-mcp doctor`."
        )


class EditsBadReply(EditsError):
    """Edits sent something that is not a usable MCP reply (malformed or over the size limit)."""

    code = -32004

    def explain(self, url: str) -> str:
        return f"Edits sent an unusable reply: {self}"
