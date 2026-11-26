"""Security regression tests (see docs/THREAT_MODEL.md). Loopback sockets and test tokens only."""

from __future__ import annotations

import socket
import subprocess
import sys
from typing import Any

import pytest

from instagram_edits_mcp.config import Endpoint
from instagram_edits_mcp.edits.client import EditsConnection
from instagram_edits_mcp.errors import EditsBadReply, EditsImpersonated, EditsRejected, EditsUnavailable
from instagram_edits_mcp.security.identity import ServerVerifier
from instagram_edits_mcp.security.keychain import Token
from tests.support.edits_double import TEST_TOKEN, Behaviour, EditsDouble, Sink
from tests.support.stdio import McpProcess, unused_port

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs the real lsof and codesign")


def connect(url: str, token: str | None = TEST_TOKEN, **options: Any) -> EditsConnection:
    return EditsConnection(Endpoint.parse(url), Token(token, "environment") if token else None, **options)


def impostor_verifier() -> ServerVerifier:
    def machine(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0 if argv[0].endswith("lsof") else 3, "4321", "")

    return ServerVerifier("enforce", "com.burbn.basel.mac", "777W53UFB2", runner=machine, platform="darwin")


class TestNetworkBoundaries:
    """T1-T3: the token only ever travels to the loopback Edits address."""

    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
    @pytest.mark.parametrize("target", ["remote", "local"])
    def test_redirects_are_not_followed(self, status: int, target: str) -> None:
        with Sink() as sink:
            location = "http://198.51.100.9/collect" if target == "remote" else f"{sink.url}/collect"
            with EditsDouble(Behaviour(redirect=(status, location))) as edits:
                with pytest.raises(EditsRejected) as raised:
                    connect(edits.url).send(PING)
            assert raised.value.status == status
        assert sink.hits == 0

    def test_proxy_variables_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with Sink() as proxy, EditsDouble() as edits:
            for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
                monkeypatch.setenv(name, proxy.url)
            assert connect(edits.url).send(PING)[0]["result"] == {}
        assert proxy.hits == 0 and edits.record.authorizations == [f"Bearer {TEST_TOKEN}"]

    def test_server_process_ignores_proxy_variables(self) -> None:
        with Sink() as proxy, EditsDouble() as edits:
            mcp = McpProcess(edits.url, HTTP_PROXY=proxy.url, http_proxy=proxy.url, ALL_PROXY=proxy.url)
            assert mcp.handshake()["result"]
            mcp.stop()
        assert proxy.hits == 0

    @pytest.mark.parametrize("url", ["http://example.com:80/mcp", "http://localhost:19501/mcp", "https://127.0.0.1:1/"])
    def test_server_process_refuses_to_start_with_a_remote_address(self, url: str) -> None:
        mcp = McpProcess(url)
        stderr = mcp.stop()
        assert mcp.proc.returncode == 2 and "Edits address" in stderr and TEST_TOKEN not in stderr

    def test_nothing_is_sent_if_the_socket_lands_off_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with EditsDouble() as edits:
            monkeypatch.setattr(socket.socket, "getpeername", lambda _s: ("203.0.113.5", 80))
            with pytest.raises(EditsImpersonated):
                connect(edits.url).send(PING)
        assert edits.record.authorizations == []


class TestImpersonation:
    """T4: a program squatting on the Edits port never receives the token."""

    def test_verifier_blocks_the_request(self) -> None:
        with EditsDouble() as squatter:
            with pytest.raises(EditsImpersonated, match="was not sent"):
                connect(squatter.url, verifier=impostor_verifier()).send(PING)
        assert squatter.record.authorizations == []

    @macos_only
    def test_real_codesign_rejects_a_python_listener(self) -> None:
        with EditsDouble() as squatter:
            verifier = ServerVerifier("enforce", "com.burbn.basel.mac", "777W53UFB2")
            with pytest.raises(EditsImpersonated):
                connect(squatter.url, verifier=verifier).send(PING)
        assert squatter.record.authorizations == []

    @macos_only
    def test_server_process_stays_detached_instead_of_talking_to_a_squatter(self) -> None:
        with EditsDouble() as squatter:
            mcp = McpProcess(squatter.url, INSTAGRAM_EDITS_MCP_VERIFY_SERVER="enforce",
                             INSTAGRAM_EDITS_MCP_RETRY_SECONDS="0.2")
            init = mcp.handshake()
            assert init["result"]["serverInfo"]["name"] == "instagram-edits-mcp"
            text = mcp.tool("edits_connection_status", timeout=20)["result"]["content"][0]["text"]
            assert "not Instagram Edits" in text
            stderr = mcp.stop()
        assert squatter.record.authorizations == [] and TEST_TOKEN not in stderr


class TestSecretsStayHidden:
    """T5, T6: the token stays out of argv, logs, errors and reprs."""

    def test_echoed_token_is_not_relayed_or_logged(self) -> None:
        with EditsDouble(Behaviour(echo_auth_with_status=500)) as edits:
            mcp = McpProcess(edits.url)
            error = mcp.request("initialize", {})["error"]
            assert error["code"] == -32002 and "HTTP 500" in error["message"]
            stderr = mcp.stop()
        assert TEST_TOKEN not in mcp.transcript() + stderr
        assert "received" not in mcp.transcript(), "error bodies are never relayed"

    def test_wrong_token_is_not_logged(self) -> None:
        with EditsDouble() as edits:
            mcp = McpProcess(edits.url, token="incorrect-token-value-1")
            error = mcp.request("initialize", {})["error"]
            assert error["code"] == -32002 and "set-token" in error["message"]
            stderr = mcp.stop()
        assert "incorrect-token-value-1" not in mcp.transcript() + stderr

    def test_token_is_not_on_the_command_line(self) -> None:
        with EditsDouble() as edits:
            mcp = McpProcess(edits.url)
            mcp.handshake()
            command_line = subprocess.run(["/bin/ps", "-o", "args=", "-p", str(mcp.proc.pid)],
                                          capture_output=True, text=True, check=False).stdout
            mcp.stop()
        assert TEST_TOKEN not in command_line

    def test_connection_repr_hides_the_token(self) -> None:
        assert TEST_TOKEN not in repr(connect("http://127.0.0.1:1/mcp"))


class TestResourceLimits:
    """T9: size limits."""

    def test_oversized_reply_is_refused(self) -> None:
        with EditsDouble(Behaviour(oversized_body=2 << 20)) as edits:
            with pytest.raises(EditsBadReply, match="larger than"):
                connect(edits.url, max_reply_bytes=1 << 20).send(PING)

    def test_oversized_input_line_is_skipped(self) -> None:
        with EditsDouble() as edits:
            mcp = McpProcess(edits.url)
            mcp.write_text("[" + "0," * (9 << 20) + "0]\n")
            assert mcp.handshake()["result"]
            assert "longer than" in mcp.stop()


def test_closed_port_is_unavailable() -> None:
    with pytest.raises(EditsUnavailable):
        connect(f"http://127.0.0.1:{unused_port()}/mcp").send(PING)
