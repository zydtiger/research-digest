"""Locate, load, and validate user configuration."""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_NAME = "research-digest"


class ConfigError(Exception):
    """A configuration file is missing or invalid."""


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    service: str
    max_concurrency: int


@dataclass(frozen=True)
class PathConfig:
    run_dir: Path
    obsidian_vault: str
    obsidian_dir: str


@dataclass(frozen=True)
class FieldConfig:
    name: str
    arxiv_categories: tuple[str, ...]
    quota: int


@dataclass(frozen=True)
class Config:
    scoring: ModelConfig
    reading: ModelConfig
    paths: PathConfig
    fields: tuple[FieldConfig, ...]


def config_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the app directory under an absolute XDG_CONFIG_HOME, else ~/.config."""
    env = os.environ if environ is None else environ
    xdg_config_home = env.get("XDG_CONFIG_HOME", "")
    # The XDG specification requires ignoring relative values.
    if xdg_config_home and Path(xdg_config_home).is_absolute():
        return Path(xdg_config_home) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def default_config_path(environ: Mapping[str, str] | None = None) -> Path:
    return config_dir(environ) / "config.toml"


def default_env_path(environ: Mapping[str, str] | None = None) -> Path:
    return config_dir(environ) / "env"


def load_config(path: Path | None = None) -> Config:
    config_path = default_config_path() if path is None else path
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"config file not found: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {config_path}: {error}") from error

    models = _table(data, "models", "models")
    paths = _table(data, "paths", "paths")
    return Config(
        scoring=_model(models, "scoring"),
        reading=_model(models, "reading"),
        paths=PathConfig(
            run_dir=Path(_value(paths, "run_dir", str, "paths")).expanduser(),
            obsidian_vault=_value(paths, "obsidian_vault", str, "paths"),
            obsidian_dir=_value(paths, "obsidian_dir", str, "paths"),
        ),
        fields=_fields(data),
    )


def load_env(
    path: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Load KEY=VALUE secrets from a private file, keeping variables already set."""
    env = os.environ if environ is None else environ
    env_path = default_env_path(env) if path is None else path
    try:
        mode = env_path.stat().st_mode
    except FileNotFoundError:
        return
    if stat.S_IMODE(mode) & 0o077:
        raise ConfigError(f"env file must not be group or world accessible: {env_path}")

    for number, raw_line in enumerate(env_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            # The line may hold a secret, so report only its position.
            raise ConfigError(f"expected KEY=VALUE at {env_path}:{number}")
        env.setdefault(key.strip(), value.strip())


def _model(models: Mapping[str, Any], name: str) -> ModelConfig:
    context = f"models.{name}"
    table = _table(models, name, context)
    max_concurrency = _value(table, "max_concurrency", int, context)
    if max_concurrency < 1:
        raise ConfigError(f"{context}.max_concurrency must be at least 1")
    return ModelConfig(
        base_url=_value(table, "base_url", str, context),
        model=_value(table, "model", str, context),
        service=_value(table, "service", str, context),
        max_concurrency=max_concurrency,
    )


def _fields(data: Mapping[str, Any]) -> tuple[FieldConfig, ...]:
    tables = data.get("fields")
    if not isinstance(tables, list) or not tables:
        raise ConfigError("fields must contain at least one [[fields]] table")

    fields = []
    for index, table in enumerate(tables):
        context = f"fields[{index}]"
        if not isinstance(table, dict):
            raise ConfigError(f"{context} must be a table")
        categories = _value(table, "arxiv_categories", list, context)
        if not all(isinstance(category, str) for category in categories):
            raise ConfigError(f"{context}.arxiv_categories must contain only strings")
        quota = _value(table, "quota", int, context)
        if quota < 0:
            raise ConfigError(f"{context}.quota must not be negative")
        fields.append(
            FieldConfig(
                name=_value(table, "name", str, context),
                arxiv_categories=tuple(categories),
                quota=quota,
            )
        )
    return tuple(fields)


def _table(data: Mapping[str, Any], key: str, context: str) -> Mapping[str, Any]:
    table = data.get(key)
    if not isinstance(table, dict):
        raise ConfigError(f"missing required table: {context}")
    return table


def _value[T](table: Mapping[str, Any], key: str, kind: type[T], context: str) -> T:
    if key not in table:
        raise ConfigError(f"missing required setting: {context}.{key}")
    value = table[key]
    # TOML booleans are ints in Python, so an exact type check rejects them.
    if type(value) is not kind or value == "":
        raise ConfigError(f"{context}.{key} must be a non-empty {kind.__name__}")
    return value
