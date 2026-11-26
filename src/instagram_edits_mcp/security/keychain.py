"""The Edits access token: where it comes from and how it is kept out of sight.

Sources, in order:
1. `INSTAGRAM_EDITS_MCP_TOKEN`. The Claude Code plugin sets this from
   a `sensitive` user option, which Claude Code itself keeps in the macOS keychain.
2. The macOS keychain item written by `instagram-edits-mcp set-token`.

The token is never written to a file, never passed on a command line (where `ps` could see it) and
never printed: `Token` hides it from `repr`/`str`, and every value loaded is registered with the
redactor so it is masked if it ever turns up in a diagnostic.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..config import ENV_TOKEN, Config, env_text
from ..errors import CredentialError
from .redaction import REDACTOR

SECURITY = "/usr/bin/security"
# Edits tokens are URL-safe strings. Anything else is refused, which also rules out injection into the
# `security -i` command line used to store it.
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/=:-]{8,4096}")
_NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True, slots=True)
class Token:
    _value: str = field(repr=False)
    source: str  # "environment" or "keychain"

    def __post_init__(self) -> None:
        REDACTOR.register(self._value)

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Token(source={self.source!r}, value=<redacted>)"

    __str__ = __repr__


def validate_token(value: str) -> str:
    value = value.strip()
    if not TOKEN_PATTERN.fullmatch(value):
        raise CredentialError(
            "that does not look like an Edits access token (expected 8-4096 URL-safe characters, no spaces)"
        )
    return value


class Keychain:
    """A generic-password item in the user's login keychain, driven through /usr/bin/security."""

    def __init__(self, service: str, account: str, runner: Runner = subprocess.run) -> None:
        if not (_NAME_PATTERN.fullmatch(service) and _NAME_PATTERN.fullmatch(account)):
            raise CredentialError("keychain service and account names must be simple identifiers")
        self.service = service
        self.account = account
        self._run = runner

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin"

    def get(self) -> str | None:
        try:
            done = self._run(
                [SECURITY, "find-generic-password", "-s", self.service, "-a", self.account, "-w"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        value = done.stdout.strip()
        return value or None

    def set(self, value: str) -> None:
        value = validate_token(value)
        # The secret goes in on stdin (interactive mode), never in argv. Validation above guarantees it
        # contains no quote, backslash, space or newline, so it cannot break out of the quoted argument.
        command = f'add-generic-password -U -s "{self.service}" -a "{self.account}" -l "{self.service}" -w "{value}"\n'
        try:
            done = self._run([SECURITY, "-i"], input=command, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as err:
            raise CredentialError(f"could not run {SECURITY}: {type(err).__name__}") from None
        # `security -i` reports failures on stderr but may still exit 0.
        if done.returncode != 0 or "error" in (done.stderr or "").lower():
            raise CredentialError("macOS refused to store the token in the keychain")

    def delete(self) -> bool:
        try:
            done = self._run(
                [SECURITY, "delete-generic-password", "-s", self.service, "-a", self.account],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0


def keychain_for(config: Config, runner: Runner = subprocess.run) -> Keychain | None:
    if not config.keychain or not Keychain.available():
        return None
    return Keychain(config.keychain_service, config.keychain_account, runner)


def resolve_token(env: Mapping[str, str], keychain: Keychain | None = None) -> Token | None:
    """The token from the environment, else from the keychain, else None."""
    raw = env_text(env, ENV_TOKEN)
    if raw is not None:
        return Token(raw, "environment")
    if keychain is not None and (stored := keychain.get()):
        return Token(stored, "keychain")
    return None
