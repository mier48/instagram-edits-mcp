"""Token handling. `/usr/bin/security` is replaced by a fake; the real keychain is never touched."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

import pytest

from instagram_edits_mcp.errors import CredentialError
from instagram_edits_mcp.security.keychain import Keychain, Token, resolve_token, validate_token
from instagram_edits_mcp.security.redaction import redact

SAMPLE = "sample-token-for-unit-tests-123"


@dataclass
class FakeSecurity:
    code: int = 0
    stdout: str = ""
    stderr: str = ""
    invocations: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.invocations.append({"argv": argv, **options})
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)


def test_token_never_shows_its_value() -> None:
    token = Token(SAMPLE, "keychain")
    assert SAMPLE not in f"{token} {token!r} {[token]}"
    assert token.reveal() == SAMPLE


def test_every_loaded_token_is_redacted_from_diagnostics() -> None:
    Token("token-loaded-from-somewhere", "environment")
    assert "token-loaded-from-somewhere" not in redact("echo token-loaded-from-somewhere")


@pytest.mark.parametrize("value", ["tiny", "with space", 'has"quote', "back\\slash", "line\nbreak", "é" * 10, "x" * 4097])
def test_malformed_tokens_are_refused(value: str) -> None:
    with pytest.raises(CredentialError):
        validate_token(value)


def test_storing_passes_the_secret_on_stdin_only() -> None:
    security = FakeSecurity()
    Keychain("svc", "acct", security).set(SAMPLE)
    [call] = security.invocations
    assert call["argv"] == ["/usr/bin/security", "-i"]
    assert not any(SAMPLE in arg for arg in call["argv"])
    assert call["input"].startswith("add-generic-password -U ") and SAMPLE in call["input"]


def test_storage_errors_do_not_echo_the_secret() -> None:
    security = FakeSecurity(stderr="security: SecKeychainItemCreateFromContent: error -25308")
    with pytest.raises(CredentialError) as raised:
        Keychain("svc", "acct", security).set(SAMPLE)
    assert SAMPLE not in str(raised.value)


def test_reading_and_deleting() -> None:
    security = FakeSecurity(stdout=f"{SAMPLE}\n")
    keychain = Keychain("svc", "acct", security)
    assert keychain.get() == SAMPLE
    assert security.invocations[0]["argv"][1:] == ["find-generic-password", "-s", "svc", "-a", "acct", "-w"]
    assert keychain.delete()
    assert Keychain("svc", "acct", FakeSecurity(code=44)).get() is None


def test_unsafe_item_names_are_refused() -> None:
    with pytest.raises(CredentialError):
        Keychain('svc" -w "x', "acct", FakeSecurity())


def test_environment_comes_before_keychain() -> None:
    keychain = Keychain("svc", "acct", FakeSecurity(stdout="from-the-keychain-1"))
    from_env = resolve_token({"INSTAGRAM_EDITS_MCP_TOKEN": "from-the-environment"}, keychain)
    assert from_env is not None and from_env.source == "environment"
    from_keychain = resolve_token({}, keychain)
    assert from_keychain is not None and from_keychain.reveal() == "from-the-keychain-1"
    assert resolve_token({"INSTAGRAM_EDITS_MCP_TOKEN": "${user_config.edits_token}"}) is None
