from __future__ import annotations

import subprocess
from typing import Any

import pytest

from instagram_edits_mcp.security.identity import NoListener, ServerNotTrusted, ServerVerifier, requirement


class Machine:
    """Fake lsof/codesign: `listeners` are the PIDs on the port, `signed` the PIDs that pass codesign."""

    def __init__(self, listeners: list[int], signed: set[int]) -> None:
        self.listeners = listeners
        self.signed = signed
        self.codesign_runs = 0

    def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/usr/sbin/lsof":
            return subprocess.CompletedProcess(argv, 0 if self.listeners else 1, " ".join(map(str, self.listeners)), "")
        self.codesign_runs += 1
        return subprocess.CompletedProcess(argv, 0 if int(argv[-1]) in self.signed else 3, "", "")


def verifier(machine: Any, mode: str = "enforce", log: list[str] | None = None, platform: str = "darwin") -> ServerVerifier:
    sink = log.append if log is not None else (lambda _m: None)
    return ServerVerifier(mode, "com.burbn.basel.mac", "777W53UFB2", sink, runner=machine, platform=platform)  # type: ignore[arg-type]


def test_requirement_is_pinned_and_injection_safe() -> None:
    text = requirement("com.burbn.basel.mac", "777W53UFB2")
    assert text == ('anchor apple generic and identifier "com.burbn.basel.mac" '
                    'and certificate leaf[subject.OU] = "777W53UFB2"')
    with pytest.raises(ValueError):
        requirement('a" or anchor apple', "777W53UFB2")


def test_signed_edits_is_accepted_and_the_signature_check_is_cached() -> None:
    machine = Machine([501], {501})
    v = verifier(machine)
    v.check(19501)
    v.check(19501)
    assert machine.codesign_runs == 1


@pytest.mark.parametrize(("listeners", "signed"), [([777], set()), ([501, 777], {501})])
def test_any_unsigned_listener_is_refused(listeners: list[int], signed: set[int]) -> None:
    with pytest.raises(ServerNotTrusted, match="was not sent"):
        verifier(Machine(listeners, signed)).check(19501)


def test_empty_port() -> None:
    with pytest.raises(NoListener):
        verifier(Machine([], set())).check(19501)


def test_a_new_process_on_the_port_is_checked_again() -> None:
    machine = Machine([501], {501})
    v = verifier(machine)
    v.check(19501)
    machine.listeners = [888]
    with pytest.raises(ServerNotTrusted):
        v.check(19501)


def test_warn_mode_reports_once() -> None:
    log: list[str] = []
    v = verifier(Machine([777], set()), mode="warn", log=log)
    v.check(19501)
    v.check(19501)
    assert len(log) == 1 and log[0].startswith("WARNING")


def test_off_and_non_macos_do_nothing() -> None:
    machine = Machine([777], set())
    verifier(machine, mode="off").check(19501)
    assert verifier(machine, platform="linux").mode == "off"
    assert machine.codesign_runs == 0


def test_missing_tools_mean_untrusted() -> None:
    def missing(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(argv[0])

    with pytest.raises(ServerNotTrusted):
        verifier(missing).check(19501)
