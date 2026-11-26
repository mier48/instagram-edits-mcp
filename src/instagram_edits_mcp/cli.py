"""Entry point: `instagram-edits-mcp [serve | doctor | set-token | delete-token | config]`."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Callable, Sequence

from . import SERVER_NAME, __version__
from .config import load_config
from .diagnostics import diagnose
from .edits.client import EditsConnection
from .errors import ConfigError, CredentialError
from .relay.server import Relay
from .security.identity import ServerVerifier
from .security.keychain import Keychain, keychain_for, resolve_token, validate_token
from .security.policy import ToolPolicy
from .security.redaction import Logger, redact

log = Logger()
Command = Callable[[argparse.Namespace], int]


def serve(args: argparse.Namespace) -> int:
    config = load_config()
    for warning in config.warnings:
        log(f"WARNING: {warning}")
    token = resolve_token(os.environ, keychain_for(config))
    if token is None:
        log(f"no access token found; Edits will refuse requests until you run `{SERVER_NAME} set-token`")
    policy = ToolPolicy.from_config(config)
    if not policy.unrestricted:
        log(f"tool policy: {policy.describe()}")
    edits = EditsConnection(
        config.endpoint,
        token,
        verifier=ServerVerifier.from_config(config, log),
        timeout=config.timeout_seconds,
        max_reply_bytes=config.max_response_bytes,
    )
    relay = Relay(
        edits,
        policy,
        sys.stdout,
        log,
        track_tools=config.track_tools and not args.no_tool_tracking,
        follow_stream=config.follow_stream and not args.no_stream,
        retry_seconds=config.retry_seconds,
    )
    return relay.run(sys.stdin)


def doctor(args: argparse.Namespace) -> int:
    diagnosis = diagnose()
    print(diagnosis.to_json() if args.json else diagnosis.to_text())
    return 0 if diagnosis.healthy else 1


def _keychain() -> Keychain:
    keychain = keychain_for(load_config())
    if keychain is None:
        raise CredentialError("the macOS keychain is unavailable (not macOS, or disabled in the configuration)")
    return keychain


def set_token(_args: argparse.Namespace) -> int:
    keychain = _keychain()
    if not sys.stdin.isatty():
        raise CredentialError("set-token must be run in a terminal; the token is never read from a pipe or an argument")
    print("Paste the access token shown in Edits > Settings > Automation. Typing is hidden.")
    keychain.set(validate_token(getpass.getpass("Access token: ")))
    print(f"Stored in the macOS keychain as '{keychain.service}' / '{keychain.account}'.")
    print(f"Check the connection with `{SERVER_NAME} doctor`.")
    return 0


def delete_token(_args: argparse.Namespace) -> int:
    removed = _keychain().delete()
    print("Token removed from the macOS keychain." if removed else "There was no token in the keychain.")
    return 0 if removed else 1


def show_config(_args: argparse.Namespace) -> int:
    print(json.dumps(load_config().public(), indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog=SERVER_NAME, description="Hardened local MCP server for Instagram Edits for Mac.")
    root.add_argument("--version", action="version", version=f"{SERVER_NAME} {__version__}")
    commands = root.add_subparsers(dest="command", metavar="COMMAND")

    run = commands.add_parser("serve", help="run the MCP server on stdio (default)")
    run.add_argument("--no-stream", action="store_true", help="do not subscribe to Edits' notification stream")
    run.add_argument("--no-tool-tracking", action="store_true", help="do not re-check the tool set after calls")
    run.set_defaults(handler=serve)

    check = commands.add_parser("doctor", help="diagnose the connection to Edits")
    check.add_argument("--json", action="store_true", help="JSON output for bug reports (contains no secrets)")
    check.set_defaults(handler=doctor)

    commands.add_parser("set-token", help="store the access token in the macOS keychain").set_defaults(handler=set_token)
    commands.add_parser("delete-token", help="remove the access token from the keychain").set_defaults(
        handler=delete_token
    )
    commands.add_parser("config", help="print the effective configuration").set_defaults(handler=show_config)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handler: Command | None = getattr(args, "handler", None)
    if handler is None:
        args = parser().parse_args(["serve", *(argv or [])])
        handler = args.handler
    try:
        return int(handler(args))
    except (ConfigError, CredentialError) as err:
        log(f"error: {err}")
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as err:  # never let a traceback (and its locals) reach the client's logs
        log(f"unexpected {type(err).__name__}: {redact(err)}")
        return 1
