from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from instagram_edits_mcp import diagnostics
from instagram_edits_mcp.security.identity import ServerVerifier
from tests.support.edits_double import TEST_TOKEN, EditsDouble

CLOSED = "http://127.0.0.1:9/mcp"


def environment(url: str, token: str | None = TEST_TOKEN, **extra: str) -> dict[str, str]:
    env = {"HOME": "/nonexistent", "INSTAGRAM_EDITS_MCP_URL": url, "INSTAGRAM_EDITS_MCP_KEYCHAIN": "off",
           "INSTAGRAM_EDITS_MCP_VERIFY_SERVER": "off"}
    if token:
        env["INSTAGRAM_EDITS_MCP_TOKEN"] = token
    return env | extra


def levels(d: diagnostics.Diagnosis) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in d.findings:
        out.setdefault(f.check, f.level)
    return out


def detail(d: diagnostics.Diagnosis, check: str) -> str:
    return next(f.detail for f in d.findings if f.check == check)


@pytest.fixture(autouse=True)
def installed_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics, "find_edits_app", lambda: Path("/Applications/Edits.app"))


def test_healthy_chain() -> None:
    with EditsDouble() as edits:
        d = diagnostics.diagnose(environment(edits.url))
    found = levels(d)
    assert found["handshake"] == found["tools"] == "ok"
    assert found["server identity"] == "warn", "disabled verification must stand out"
    assert "advertised but never sent" in detail(d, "tool changes")
    assert detail(d, "automation schema") == "test-schema-1"
    assert "setSpeedCurve" in detail(d, "speed curves")
    assert TEST_TOKEN not in d.to_text() + d.to_json()


def test_policy_is_summarised() -> None:
    with EditsDouble() as edits:
        d = diagnostics.diagnose(environment(edits.url, INSTAGRAM_EDITS_MCP_READ_ONLY="1"))
    assert detail(d, "tool policy").startswith("read-only") and "3 of 5 visible" in detail(d, "tool policy")


def test_nothing_listening() -> None:
    d = diagnostics.diagnose(environment(CLOSED))
    assert levels(d)["automation server"] == "fail" and not d.healthy


def test_refused_token() -> None:
    with EditsDouble() as edits:
        d = diagnostics.diagnose(environment(edits.url, token="not-the-right-token"))
    assert levels(d)["handshake"] == "fail" and "set-token" in detail(d, "handshake")


def test_missing_token() -> None:
    with EditsDouble() as edits:
        d = diagnostics.diagnose(environment(edits.url, token=None))
    assert levels(d)["access token"] == "fail" and "none in the environment" in detail(d, "access token")


def test_invalid_configuration() -> None:
    d = diagnostics.diagnose({"INSTAGRAM_EDITS_MCP_URL": "http://attacker.example:1/"})
    assert levels(d) == {"configuration": "fail"}


def test_untrusted_server_stops_the_chain_before_any_token_is_sent() -> None:
    def impostor(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0 if argv[0].endswith("lsof") else 3, "4321", "")

    verifier = ServerVerifier("enforce", "com.burbn.basel.mac", "777W53UFB2", runner=impostor, platform="darwin")
    with EditsDouble() as edits:
        d = diagnostics.diagnose(environment(edits.url, INSTAGRAM_EDITS_MCP_VERIFY_SERVER="enforce"), verifier)
        assert edits.record.authorizations == []
    assert levels(d)["server identity"] == "fail" and "handshake" not in levels(d)


def test_app_version_is_read_from_the_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "Edits.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": "457.0.0"}, fh)
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(diagnostics, "find_edits_app", lambda: app)
    d = diagnostics.diagnose(environment(CLOSED))
    assert detail(d, "Edits app") == f"457.0.0 at {app}"
    assert diagnostics.bundle_version(tmp_path / "Missing.app") is None
