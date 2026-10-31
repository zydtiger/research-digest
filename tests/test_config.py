from __future__ import annotations

from pathlib import Path

import pytest

from research_digest.cli import main
from research_digest.config import (
    ConfigError,
    default_config_path,
    default_env_path,
    load_config,
    load_env,
)

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"


def test_default_path_is_under_home_config_on_every_platform() -> None:
    assert default_config_path({}) == Path.home() / ".config/research-digest/config.toml"
    assert default_env_path({}) == Path.home() / ".config/research-digest/env"


def test_absolute_xdg_config_home_overrides_default() -> None:
    environ = {"XDG_CONFIG_HOME": "/custom/config"}
    assert default_config_path(environ) == Path("/custom/config/research-digest/config.toml")


@pytest.mark.parametrize("value", ["", "relative/config"])
def test_empty_or_relative_xdg_config_home_is_ignored(value: str) -> None:
    environ = {"XDG_CONFIG_HOME": value}
    assert default_config_path(environ) == Path.home() / ".config/research-digest/config.toml"


def test_example_config_is_valid() -> None:
    config = load_config(EXAMPLE)
    assert config.scoring.max_concurrency == 4
    assert config.fields


def test_missing_model_name_is_an_error(tmp_path: Path) -> None:
    text = EXAMPLE.read_text().replace('model = "local-scoring-model"\n', "", 1)
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=r"models\.scoring\.model"):
        load_config(path)


def test_missing_config_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_env_file_keeps_existing_variables(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text("# comment\nFROM_FILE=file\nALREADY_SET=file\n")
    path.chmod(0o600)
    environ = {"ALREADY_SET": "shell"}
    load_env(path, environ)
    assert environ == {"ALREADY_SET": "shell", "FROM_FILE": "file"}


def test_missing_env_file_is_allowed(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    load_env(tmp_path / "missing", environ)
    assert environ == {}


def test_readable_env_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text("KEY=value\n")
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="accessible"):
        load_env(path, {})


def test_malformed_env_line_does_not_reveal_its_content(tmp_path: Path) -> None:
    path = tmp_path / "env"
    path.write_text("not-a-secret-assignment\n")
    path.chmod(0o600)
    with pytest.raises(ConfigError) as error:
        load_env(path, {})
    assert "not-a-secret-assignment" not in str(error.value)


def test_cli_path_honors_xdg_config_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["config", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "research-digest/config.toml")


def test_cli_check_accepts_explicit_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["--config", str(EXAMPLE), "config", "check"]) == 0


def test_cli_check_reports_missing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["config", "check"]) == 2
    assert "not found" in capsys.readouterr().err
