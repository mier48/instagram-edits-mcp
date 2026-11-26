from __future__ import annotations

import io

import pytest

from instagram_edits_mcp.security.redaction import MASK, REDACTOR, Logger, Redactor


def test_registered_values_are_masked_everywhere() -> None:
    redactor = Redactor()
    redactor.register("registered-value-42")
    assert redactor.redact("a registered-value-42 b registered-value-42") == f"a {MASK} b {MASK}"


def test_very_short_values_are_not_registered() -> None:
    redactor = Redactor()
    redactor.register("ab")
    assert redactor.redact("ab") == "ab"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def-ghi-jkl", "abc.def-ghi-jkl"),
        ("authorization=xyz123456", "xyz123456"),
        ('{"access_token": "q1w2e3r4t5"}', "q1w2e3r4t5"),
        ("url?token=abcdefgh123&x=1", "abcdefgh123"),
        ("password: hunter2hunter2", "hunter2hunter2"),
        ("Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ],
)
def test_credential_shapes_are_masked(text: str, secret: str) -> None:
    assert secret not in Redactor().redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "access token: none in the environment",
        "Edits refused the access token (HTTP 403). Copy the current token from Edits",
        "tokens: 3",
    ],
)
def test_ordinary_prose_is_left_alone(text: str) -> None:
    assert Redactor().redact(text) == text


def test_logger_prefixes_and_redacts() -> None:
    REDACTOR.register("logger-visible-secret")
    out = io.StringIO()
    Logger(out)("leak? logger-visible-secret")
    assert out.getvalue() == f"[instagram-edits-mcp] leak? {MASK}\n"
