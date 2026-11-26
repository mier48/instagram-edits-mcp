"""`instagram-edits-mcp doctor`: walk the chain client -> relay -> Edits and report each link.

The checks run as an ordered list of steps over a shared `Probe`. A step records findings and returns
False to stop the chain when later steps cannot run (no server, untrusted server, no token...). Every
detail goes through the redactor, and the token itself is never part of a finding, so the JSON form is
safe to attach to a bug report.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import SERVER_NAME, __version__
from .config import Config, load_config
from .edits import patches
from .edits.client import EditsConnection
from .errors import ConfigError, EditsError, EditsRejected, EditsUnavailable
from .security.identity import ServerVerifier
from .security.keychain import Token, keychain_for, resolve_token
from .security.policy import ToolPolicy
from .security.redaction import redact

Level = Literal["ok", "warn", "fail"]
APP_LOCATIONS = (Path("/Applications/Edits.app"), Path.home() / "Applications" / "Edits.app")
MDFIND = "/usr/bin/mdfind"
ENABLE_HINT = "In Edits: Settings > Automation > Enable Automation Server"


@dataclass
class Finding:
    check: str
    level: Level
    detail: str


@dataclass
class Diagnosis:
    version: str = __version__
    findings: list[Finding] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(f.level != "fail" for f in self.findings)

    def to_json(self) -> str:
        return redact(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    def to_text(self) -> str:
        tag = {"ok": "[ ok ]", "warn": "[warn]", "fail": "[FAIL]"}
        rows = [f"{SERVER_NAME} {self.version}: diagnosis", ""]
        rows += [f"{tag[f.level]} {f.check}: {f.detail}" for f in self.findings]
        rows += ["", "Everything needed is in place." if self.healthy else "Fix the [FAIL] lines, then run doctor again."]
        return redact("\n".join(rows))


@dataclass
class Probe:
    env: Mapping[str, str]
    diagnosis: Diagnosis
    verifier: ServerVerifier | None = None
    config: Config | None = None
    token: Token | None = None
    edits: EditsConnection | None = None
    tools_result: dict[str, Any] = field(default_factory=dict)
    advertises_changes: bool = False

    def note(self, check: str, level: Level, detail: str) -> None:
        self.diagnosis.findings.append(Finding(check, level, redact(detail)))

    def ask(self, rid: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.edits is not None
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        replies = self.edits.send(message)
        return next((r for r in replies if isinstance(r, dict) and r.get("id") == rid), {})


Step = Callable[[Probe], bool]


def step_config(p: Probe) -> bool:
    try:
        p.config = load_config(p.env)
    except ConfigError as err:
        p.note("configuration", "fail", str(err))
        return False
    p.note("configuration", "ok", f"read from {p.config.source}" if p.config.source else "defaults (no config file)")
    for warning in p.config.warnings:
        p.note("configuration", "warn", warning)
    return True


def find_edits_app() -> Path | None:
    for path in APP_LOCATIONS:
        if path.exists():
            return path
    if not Path(MDFIND).exists():
        return None
    try:
        found = subprocess.run(
            [MDFIND, "kMDItemCFBundleIdentifier == 'com.burbn.basel.mac'"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.split("\n")[0].strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(found) if found else None


def bundle_version(app: Path) -> str | None:
    try:
        with (app / "Contents" / "Info.plist").open("rb") as fh:
            value = plistlib.load(fh).get("CFBundleShortVersionString")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return str(value) if value else None


def step_platform(p: Probe) -> bool:
    if platform.system() != "Darwin":
        p.note("platform", "warn", f"{platform.system()}: Instagram Edits only exists for macOS")
        p.note("Edits app", "fail", "not available on this platform")
        return True
    p.note("platform", "ok", f"macOS {platform.mac_ver()[0]}")
    app = find_edits_app()
    if app is None:
        p.note("Edits app", "fail", "not installed (edits.instagram.com/desktop)")
    else:
        version = bundle_version(app)
        p.note("Edits app", "ok", f"{version} at {app}" if version else str(app))
    return True


def step_identity(p: Probe) -> bool:
    assert p.config is not None
    p.verifier = p.verifier or ServerVerifier.from_config(p.config)
    if p.verifier.mode == "off":
        p.note("server identity", "warn", "not checked (verify = off)")
        return True
    try:
        report = p.verifier.inspect(p.config.endpoint.port)
    except Exception as err:
        p.note("server identity", "fail", f"could not be checked: {err}")
        return False
    if not report.pids:
        p.note("automation server", "fail", f"nothing listens at {p.config.endpoint}. {ENABLE_HINT}")
        return False
    if report.trusted:
        p.note("server identity", "ok", report.detail)
        return True
    p.note("server identity", "fail" if p.verifier.mode == "enforce" else "warn", report.detail)
    return p.verifier.mode != "enforce"


def step_reachable(p: Probe) -> bool:
    assert p.config is not None
    anonymous = EditsConnection(p.config.endpoint, None, timeout=5)
    try:
        anonymous.send({"jsonrpc": "2.0", "id": 0, "method": "ping"})
        p.note("automation server", "ok", f"answering at {p.config.endpoint}")
    except EditsRejected as err:
        p.note("automation server", "ok", f"answering at {p.config.endpoint} (HTTP {err.status} without a token)")
    except EditsUnavailable:
        p.note("automation server", "fail", f"nothing answers at {p.config.endpoint}. {ENABLE_HINT}")
        return False
    except EditsError as err:
        p.note("automation server", "warn", f"answering, but: {err}")
    return True


def step_token(p: Probe) -> bool:
    assert p.config is not None
    keychain = keychain_for(p.config)
    p.token = resolve_token(p.env, keychain)
    if p.token is None:
        where = "environment or keychain" if keychain else "environment (keychain disabled)"
        p.note("access token", "fail", f"none in the {where}; run `{SERVER_NAME} set-token`")
        return False
    p.note("access token", "ok", f"found in the {p.token.source}")
    return True


def step_handshake(p: Probe) -> bool:
    assert p.config is not None
    p.edits = EditsConnection(p.config.endpoint, p.token, verifier=p.verifier, timeout=15)
    try:
        reply = p.ask(1, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": f"{SERVER_NAME}-doctor", "version": __version__},
        })
    except EditsRejected as err:
        detail = f"token refused (HTTP {err.status}); run `{SERVER_NAME} set-token`" if err.is_auth else str(err)
        p.note("handshake", "fail", detail)
        return False
    except EditsError as err:
        p.note("handshake", "fail", str(err))
        return False
    result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
    server = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else None
    if server is None:
        p.note("handshake", "fail", "Edits did not complete the MCP handshake")
        return False
    p.advertises_changes = bool(((result.get("capabilities") or {}).get("tools") or {}).get("listChanged"))
    p.note("handshake", "ok", f"{server.get('name', '?')} {server.get('version', '')} (protocol {p.edits.session.protocol})")
    return True


def step_tools(p: Probe) -> bool:
    assert p.edits is not None and p.config is not None
    try:
        p.edits.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        result = p.ask(2, "tools/list").get("result")
    except EditsError as err:
        p.note("tools", "fail", str(err))
        return False
    p.tools_result = result if isinstance(result, dict) else {}
    tools = [t for t in p.tools_result.get("tools") or [] if isinstance(t, dict)]
    screen = "a project is open" if any(t.get("name") == "addText" for t in tools) else "home screen"
    p.note("tools", "ok" if tools else "warn", f"{len(tools)} offered ({screen})")
    policy = ToolPolicy.from_config(p.config)
    if not policy.unrestricted:
        visible = policy.filter_tools_result(p.tools_result).get("tools", [])
        p.note("tool policy", "ok", f"{policy.describe()}: {len(visible)} of {len(tools)} visible")
    issues = patches.cache_hint_issues(p.tools_result)
    tagged = p.tools_result.get("resultType") == "complete"
    p.note("reply format", "ok", "normalised for clients that reject it" if issues or tagged else "standard")
    return True


def step_capabilities(p: Probe) -> bool:
    names = {t.get("name") for t in p.tools_result.get("tools") or [] if isinstance(t, dict)}
    if "getCapabilities" not in names:
        return True
    try:
        reply = p.ask(3, "tools/call", {"name": "getCapabilities", "arguments": {}})
        text = "".join(b.get("text", "") for b in (reply.get("result") or {}).get("content") or [] if isinstance(b, dict))
        caps = json.loads(text)
    except (EditsError, ValueError):
        return True
    if not isinstance(caps, dict):
        return True
    if schema := caps.get("schemaVersion"):
        p.note("automation schema", "ok", str(schema)[:80])
    for catalog in caps.get("catalogs") or []:
        if isinstance(catalog, dict) and catalog.get("kind") == patches.SPEED_KIND:
            target = catalog.get("applyTool")
            fixed = target == patches.DEPRECATED_SPEED_TOOL
            p.note("speed curves", "ok", f"catalog retargeted to {patches.SPEED_TOOL}" if fixed else f"catalog uses {str(target)[:80]}")
    return True


def step_notifications(p: Probe) -> bool:
    assert p.edits is not None
    if p.edits.offers_stream():
        p.note("tool changes", "ok", "Edits streams notifications")
    else:
        detail = "advertised but never sent by Edits" if p.advertises_changes else "not announced by Edits"
        p.note("tool changes", "ok", f"{detail}; the relay re-checks after every tool call")
    return True


STEPS: tuple[Step, ...] = (
    step_config, step_platform, step_identity, step_reachable, step_token,
    step_handshake, step_tools, step_capabilities, step_notifications,
)


def diagnose(env: Mapping[str, str] | None = None, verifier: ServerVerifier | None = None) -> Diagnosis:
    probe = Probe(os.environ if env is None else env, Diagnosis(), verifier=verifier)
    for step in STEPS:
        if not step(probe):
            break
    return probe.diagnosis
