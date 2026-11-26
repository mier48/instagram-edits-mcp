from __future__ import annotations

from pathlib import Path

import pytest

from instagram_edits_mcp.config import MIB, Endpoint, load_config
from instagram_edits_mcp.errors import ConfigError

NO_HOME = {"HOME": "/nonexistent"}


def config_file(tmp_path: Path, body: str, mode: int = 0o600) -> dict[str, str]:
    path = tmp_path / "settings.toml"
    path.write_text(body)
    path.chmod(mode)
    return {"INSTAGRAM_EDITS_MCP_CONFIG": str(path)}


class TestEndpoint:
    @pytest.mark.parametrize(
        ("raw", "shown"),
        [
            ("http://127.0.0.1:19501/mcp", "http://127.0.0.1:19501/mcp"),
            ("http://127.9.9.9:80", "http://127.9.9.9:80/"),
            ("http://[::1]:19501/mcp", "http://[::1]:19501/mcp"),
        ],
    )
    def test_accepts_loopback(self, raw: str, shown: str) -> None:
        assert str(Endpoint.parse(raw)) == shown

    @pytest.mark.parametrize(
        "raw",
        [
            "http://example.com:19501/mcp",
            "http://localhost:19501/mcp",
            "https://127.0.0.1:19501/mcp",
            "file:///etc/hosts",
            "ftp://127.0.0.1:21/",
            "http://127.0.0.1/mcp",
            "http://me:pw@127.0.0.1:1/",
            "http://127.0.0.1:1/?q=1",
            "http://127.0.0.1:1/#frag",
            "http://192.168.1.10:19501/",
            "http://0.0.0.0:19501/",
            "http://[::ffff:127.0.0.1]:19501/",
            "http://127.1:19501/",
            "http://2130706433:19501/",
            "http://127.0.0.1:70000/",
            "http://127.0.0.1:1/a b",
            "",
        ],
    )
    def test_refuses_everything_else(self, raw: str) -> None:
        with pytest.raises(ConfigError):
            Endpoint.parse(raw)


def test_defaults_are_strict() -> None:
    config = load_config(NO_HOME)
    assert str(config.endpoint) == "http://127.0.0.1:19501/mcp"
    assert (config.verify, config.expected_team_id, config.read_only) == ("enforce", "777W53UFB2", False)
    assert config.warnings == ()


def test_environment_overrides() -> None:
    config = load_config(NO_HOME | {
        "INSTAGRAM_EDITS_MCP_URL": "http://127.0.0.1:4000/x",
        "INSTAGRAM_EDITS_MCP_READ_ONLY": "yes",
        "INSTAGRAM_EDITS_MCP_DENY_TOOLS": "deleteProject, export*",
        "INSTAGRAM_EDITS_MCP_RETRY_SECONDS": "0.25",
    })
    assert config.endpoint.port == 4000 and config.read_only
    assert config.deny == ("deleteProject", "export*") and config.retry_seconds == 0.25


def test_unset_plugin_placeholders_are_ignored() -> None:
    assert load_config(NO_HOME | {"INSTAGRAM_EDITS_MCP_READ_ONLY": "${user_config.read_only}"}).read_only is False


@pytest.mark.parametrize("value", ["0", "-3", "301", "nan", "soon"])
def test_retry_interval_is_bounded(value: str) -> None:
    with pytest.raises(ConfigError):
        load_config(NO_HOME | {"INSTAGRAM_EDITS_MCP_RETRY_SECONDS": value})


@pytest.mark.parametrize("mode", ["warn", "off"])
def test_weaker_verification_produces_a_warning(mode: str) -> None:
    config = load_config(NO_HOME | {"INSTAGRAM_EDITS_MCP_VERIFY_SERVER": mode})
    assert config.verify == mode and "may reach a program that is not Edits" in config.warnings[0]


def test_file_settings(tmp_path: Path) -> None:
    env = config_file(tmp_path, """
[server]
url = "http://127.0.0.1:5/m"
max_response_mb = 2
[policy]
deny = ["deleteProject"]
[relay]
track_tools = false
[credentials]
keychain_service = "custom-service"
""")
    config = load_config(env)
    assert config.endpoint.port == 5 and config.max_response_bytes == 2 * MIB
    assert config.deny == ("deleteProject",) and config.track_tools is False
    assert config.keychain_service == "custom-service" and config.source is not None


def test_environment_wins_but_deny_entries_accumulate(tmp_path: Path) -> None:
    env = config_file(tmp_path, '[policy]\nread_only = true\ndeny = ["a"]\n')
    config = load_config(env | {"INSTAGRAM_EDITS_MCP_READ_ONLY": "off", "INSTAGRAM_EDITS_MCP_DENY_TOOLS": "b"})
    assert config.read_only is False and config.deny == ("a", "b")


@pytest.mark.parametrize(
    "body",
    [
        "[server]\nurl = 'http://attacker.example:80/'\n",
        "[server]\nunknown = 1\n",
        "[extra]\nx = 1\n",
        "[policy]\nread_only = 'sometimes'\n",
        "[policy]\nallow = 3\n",
        "[server]\nexpected_team_id = 'abc'\n",
        "[credentials]\nkeychain_account = 'two words'\n",
        "this is = not = toml",
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_config(config_file(tmp_path, body))


def test_group_writable_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="writable by other users"):
        load_config(config_file(tmp_path, "[policy]\nread_only = true\n", mode=0o620))


def test_named_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config({"INSTAGRAM_EDITS_MCP_CONFIG": str(tmp_path / "absent.toml")})


def test_default_location_under_home(tmp_path: Path) -> None:
    folder = tmp_path / ".config" / "instagram-edits-mcp"
    folder.mkdir(parents=True)
    (folder / "config.toml").write_text("[policy]\nread_only = true\n")
    (folder / "config.toml").chmod(0o600)
    assert load_config({"HOME": str(tmp_path)}).read_only


def test_public_view_never_contains_the_token() -> None:
    shown = load_config(NO_HOME | {"INSTAGRAM_EDITS_MCP_TOKEN": "very-secret-token-value"}).public()
    assert "very-secret-token-value" not in str(shown)
