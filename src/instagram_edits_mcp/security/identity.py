"""Check that the process listening on the Edits port really is Edits before sending it the token.

Any unprivileged local process can listen on 127.0.0.1:19501 while Edits is closed.
Before each request the bridge asks `lsof` which processes listen on the port, then checks each one's
dynamic code signature with `codesign` against a requirement pinned to Edits' bundle identifier and
Instagram's Apple Developer Team ID. Signature results are cached per PID; `lsof` runs every time,
so a different process taking the port is noticed on the next request.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Config, VerifyMode

LSOF = "/usr/sbin/lsof"
CODESIGN = "/usr/bin/codesign"
SIGNATURE_CACHE_SECONDS = 600.0

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ServerNotTrusted(Exception):
    """The process on the Edits port is not a correctly signed Edits."""


class NoListener(Exception):
    """Nothing listens on the Edits port."""


@dataclass(frozen=True, slots=True)
class PeerReport:
    pids: tuple[int, ...]
    trusted: bool
    detail: str


def requirement(identifier: str, team_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", identifier) or not re.fullmatch(r"[A-Z0-9]{10}", team_id):
        raise ValueError("invalid identifier or team id")
    return f'anchor apple generic and identifier "{identifier}" and certificate leaf[subject.OU] = "{team_id}"'


class ServerVerifier:
    def __init__(
        self,
        mode: VerifyMode,
        identifier: str,
        team_id: str,
        log: Callable[[str], None] = lambda _m: None,
        runner: Runner = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        platform: str = sys.platform,
    ) -> None:
        self.mode: VerifyMode = mode if platform == "darwin" else "off"
        self._platform = platform
        self._requirement = requirement(identifier, team_id)
        self._log = log
        self._run = runner
        self._clock = clock
        self._lock = threading.Lock()
        self._signed: dict[int, tuple[bool, float]] = {}
        self._warned: set[int] = set()

    @classmethod
    def from_config(cls, config: Config, log: Callable[[str], None] = lambda _m: None) -> ServerVerifier:
        return cls(config.verify, config.expected_identifier, config.expected_team_id, log)

    def listeners(self, port: int) -> tuple[int, ...]:
        try:
            done = self._run(
                [LSOF, "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as err:
            raise ServerNotTrusted(f"could not run {LSOF} to identify the server ({type(err).__name__})") from None
        # lsof exits 1 when nothing matches.
        pids = tuple(sorted({int(p) for p in done.stdout.split() if p.isdigit()}))
        return pids

    def _signature_ok(self, pid: int) -> bool:
        now = self._clock()
        with self._lock:
            cached = self._signed.get(pid)
            if cached and now - cached[1] < SIGNATURE_CACHE_SECONDS:
                return cached[0]
        try:
            done = self._run(
                [CODESIGN, "--verify", f"-R={self._requirement}", str(pid)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            ok = done.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        with self._lock:
            self._signed[pid] = (ok, now)
        return ok

    def inspect(self, port: int) -> PeerReport:
        pids = self.listeners(port)
        if not pids:
            return PeerReport((), False, f"nothing is listening on port {port}")
        bad = [pid for pid in pids if not self._signature_ok(pid)]
        if bad:
            return PeerReport(
                pids,
                False,
                f"process {', '.join(map(str, bad))} on port {port} is not Instagram Edits "
                "(its code signature does not match Edits' identifier and Instagram's Team ID)",
            )
        return PeerReport(pids, True, f"Instagram Edits (pid {', '.join(map(str, pids))}, signature verified)")

    def check(self, port: int) -> None:
        """Raise ServerNotTrusted (or NoListener) unless the port belongs to Edits. No-op when verification is off."""
        if self.mode == "off":
            return
        report = self.inspect(port)
        if report.trusted:
            return
        if not report.pids:
            raise NoListener(report.detail)
        if self.mode == "warn":
            key = report.pids[0]
            if key not in self._warned:
                self._warned.add(key)
                self._log(f"WARNING: {report.detail}; continuing because verify_server is 'warn'")
            return
        raise ServerNotTrusted(report.detail + ". The access token was not sent.")
