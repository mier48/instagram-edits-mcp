"""Configuration: built-in defaults, then the TOML file, then environment variables.

Everything is validated once, here. The rest of the code only ever sees safe values; in particular the
Edits address is always plain HTTP to a literal loopback IP.
"""

from __future__ import annotations

import ipaddress
import math
import os
import re
import stat
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from .errors import ConfigError

VerifyMode = Literal["enforce", "warn", "off"]

DEFAULT_ENDPOINT = "http://127.0.0.1:19501/mcp"
EDITS_BUNDLE_ID = "com.burbn.basel.mac"
# "Developer ID Application: Instagram, Inc. (777W53UFB2)", read from Edits 457.0.0 with codesign.
EDITS_TEAM_ID = "777W53UFB2"
MIB = 1 << 20

ENV = "INSTAGRAM_EDITS_MCP_"
ENV_TOKEN = ENV + "TOKEN"
ENV_CONFIG = ENV + "CONFIG"

_IDENT = re.compile(r"[A-Za-z0-9._-]{1,128}")
_TEAM = re.compile(r"[A-Z0-9]{10}")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A validated Edits address: loopback IP, port and path."""

    host: str
    port: int
    path: str

    @classmethod
    def parse(cls, raw: str) -> Endpoint:
        try:
            parts = urlsplit(raw.strip())
            port = parts.port
        except ValueError as err:
            raise ConfigError(f"Edits address is not a valid URL ({err})") from None
        if parts.scheme != "http":
            raise ConfigError("Edits address must start with http:// (Edits only listens on loopback)")
        if "@" in parts.netloc:
            raise ConfigError("Edits address must not contain credentials")
        if parts.query or parts.fragment:
            raise ConfigError("Edits address must not contain a query or fragment")
        try:
            ip = ipaddress.ip_address(parts.hostname or "")
        except ValueError:
            raise ConfigError("Edits address must use a loopback IP such as 127.0.0.1 or ::1, not a host name") from None
        if not ip.is_loopback or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None):
            raise ConfigError("Edits address must be a loopback IP (127.0.0.0/8 or ::1)")
        if port is None or not 0 < port < 65536:
            raise ConfigError("Edits address must include a port, for example http://127.0.0.1:19501/mcp")
        path = parts.path or "/"
        if not (path.startswith("/") and path.isprintable() and " " not in path):
            raise ConfigError("Edits address has an invalid path")
        return cls(str(ip), port, path)

    def __str__(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}{self.path}"


@dataclass(frozen=True, slots=True)
class Config:
    endpoint: Endpoint = field(default_factory=lambda: Endpoint.parse(DEFAULT_ENDPOINT))
    # [server]
    verify: VerifyMode = "enforce"
    expected_identifier: str = EDITS_BUNDLE_ID
    expected_team_id: str = EDITS_TEAM_ID
    timeout_seconds: float = 600.0
    max_response_bytes: int = 64 * MIB
    # [policy]
    read_only: bool = False
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    # [relay]
    track_tools: bool = True
    follow_stream: bool = True
    retry_seconds: float = 5.0
    # [credentials]
    keychain: bool = True
    keychain_service: str = "instagram-edits-mcp"
    keychain_account: str = "default"
    # provenance
    source: Path | None = None
    warnings: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        """Every setting, for display. Config holds no secrets."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = str(value) if isinstance(value, Endpoint | Path) else list(value) if isinstance(value, tuple) else value
        return out


# -- value parsers --------------------------------------------------------------------------------------


def _flag(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(f"{key} must be true or false")


def _ranged(low: float, high: float) -> Callable[[Any, str], float]:
    def parse(value: Any, key: str) -> float:
        if isinstance(value, bool):
            raise ConfigError(f"{key} must be a number")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} must be a number") from None
        if math.isnan(number) or not low <= number <= high:
            raise ConfigError(f"{key} must be between {low:g} and {high:g}")
        return number

    return parse


def _globs(value: Any, key: str) -> tuple[str, ...]:
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list | tuple) or not all(isinstance(i, str) for i in items):
        raise ConfigError(f"{key} must be a list of tool-name patterns")
    cleaned = tuple(i.strip() for i in items if i.strip())
    if any(len(i) > 200 or not i.isprintable() for i in cleaned):
        raise ConfigError(f"{key} contains an invalid pattern")
    return cleaned


def _matching(pattern: re.Pattern[str]) -> Callable[[Any, str], str]:
    def parse(value: Any, key: str) -> str:
        if isinstance(value, str) and pattern.fullmatch(value):
            return value
        raise ConfigError(f"{key} is not valid")

    return parse


def _verify(value: Any, key: str) -> VerifyMode:
    text = str(value).strip().lower()
    if text in ("enforce", "warn", "off"):
        return text  # type: ignore[return-value]
    raise ConfigError(f"{key} must be enforce, warn or off")


def _endpoint(value: Any, key: str) -> Endpoint:
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return Endpoint.parse(value)


# (section, key) in the TOML file -> (Config field, parser, environment variable or None)
_KEYS: dict[tuple[str, str], tuple[str, Callable[[Any, str], Any], str | None]] = {
    ("server", "url"): ("endpoint", _endpoint, ENV + "URL"),
    ("server", "verify"): ("verify", _verify, ENV + "VERIFY_SERVER"),
    ("server", "expected_identifier"): ("expected_identifier", _matching(_IDENT), None),
    ("server", "expected_team_id"): ("expected_team_id", _matching(_TEAM), None),
    ("server", "timeout_seconds"): ("timeout_seconds", _ranged(1, 3600), None),
    ("server", "max_response_mb"): ("max_response_bytes", lambda v, k: int(_ranged(1, 1024)(v, k) * MIB), None),
    ("policy", "read_only"): ("read_only", _flag, ENV + "READ_ONLY"),
    ("policy", "allow"): ("allow", _globs, ENV + "ALLOW_TOOLS"),
    ("policy", "deny"): ("deny", _globs, ENV + "DENY_TOOLS"),
    ("relay", "track_tools"): ("track_tools", _flag, None),
    ("relay", "follow_stream"): ("follow_stream", _flag, None),
    ("relay", "retry_seconds"): ("retry_seconds", _ranged(0.1, 300), ENV + "RETRY_SECONDS"),
    ("credentials", "keychain"): ("keychain", _flag, ENV + "KEYCHAIN"),
    ("credentials", "keychain_service"): ("keychain_service", _matching(_IDENT), None),
    ("credentials", "keychain_account"): ("keychain_account", _matching(_IDENT), None),
}


def env_text(env: Mapping[str, str], name: str) -> str | None:
    """A set environment value, or None. Claude Code leaves `${user_config.x}` when an option is unset."""
    value = (env.get(name) or "").strip()
    return value if value and not value.startswith("${") else None


def _config_path(env: Mapping[str, str]) -> tuple[Path, bool]:
    if explicit := env_text(env, ENV_CONFIG):
        return Path(explicit).expanduser(), True
    home = env.get("HOME") or str(Path.home())
    return Path(home) / ".config" / "instagram-edits-mcp" / "config.toml", False


def _read_file(path: Path) -> dict[str, dict[str, Any]]:
    info = path.stat()
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ConfigError(f"{path} is not owned by the current user")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ConfigError(f"{path} is writable by other users; run chmod 600 on it")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as err:
        raise ConfigError(f"{path} is not valid TOML ({err})") from None
    sections = {s for s, _ in _KEYS}
    for section, table in data.items():
        if section not in sections or not isinstance(table, dict):
            raise ConfigError(f"{path}: unknown section [{section}]")
        if unknown := sorted(k for k in table if (section, k) not in _KEYS):
            raise ConfigError(f"{path}: unknown key(s) in [{section}]: {', '.join(unknown)}")
    return data


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    values: dict[str, Any] = {}
    path, explicit = _config_path(env)
    if path.is_file():
        data = _read_file(path)
        for (section, key), (name, parse, _) in _KEYS.items():
            if key in data.get(section, {}):
                values[name] = parse(data[section][key], f"{section}.{key}")
        values["source"] = path
    elif explicit:
        raise ConfigError(f"config file {path} does not exist")

    for (section, key), (name, parse, var) in _KEYS.items():
        if var and (raw := env_text(env, var)) is not None:
            parsed = parse(raw, var)
            # The environment may add to the file's deny list, never silently drop entries from it.
            values[name] = values.get("deny", ()) + parsed if name == "deny" else parsed

    config = Config(**values)
    warnings = []
    if config.verify != "enforce":
        warnings.append(f"server verification is '{config.verify}': the token may reach a program that is not Edits")
    return replace(config, warnings=tuple(warnings))
