from __future__ import annotations

import ipaddress
import os
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

PROXY_VARIABLES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests never see the developer's configuration, token, keychain or proxies."""
    for name in list(os.environ):
        if name.startswith("INSTAGRAM_EDITS_MCP_") or name.upper() in PROXY_VARIABLES:
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("INSTAGRAM_EDITS_MCP_KEYCHAIN", "off")


def _refuse_remote(sock: socket.socket, address: Any) -> None:
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    host = str(address[0] if isinstance(address, tuple) else address).split("%", 1)[0]
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise AssertionError(f"network access to {host!r} during tests; only loopback is allowed")


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any in-process attempt to reach a non-loopback address fails the test (no telemetry, no leaks)."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: Any) -> None:
        _refuse_remote(self, address)
        connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        _refuse_remote(self, address)
        return connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
