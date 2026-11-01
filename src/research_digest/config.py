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
    max_concurrency: int


@dataclass(frozen=True)
class PathConfig:
    run_dir: Path
    note_dir: Path
    parent_note: Path


@dataclass(frozen=True)
class AgentConfig:
    command: tuple[str, ...]
    language: str


@dataclass(frozen=True)
class FieldConfig:
    name: str
    arxiv_categories: tuple[str, ...]
    quota: int


@dataclass(frozen=True)
class SourcesConfig:
    attention_window_days: int
    arrival_grace_days: int
    exclude_keywords: tuple[str, ...]
    preprint_servers: tuple[str, ...]
    preprint_categories: tuple[str, ...]
    preprint_keywords: tuple[str, ...]
    hf_model_limit: int
    hn_min_points: int
    github_topics: tuple[str, ...]
    github_limit: int
    blogs: tuple[str, ...]


@dataclass(frozen=True)
class RankingConfig:
    signal_weights: Mapping[str, float]
    kind_slots: Mapping[str, int]
    min_attention: float
    attention_weight: float
    content_weight: float
    card_count: int
    global_slots: int
    novelty_slots: int


@dataclass(frozen=True)
class ReadingConfig:
    max_items: int
    max_chars: int


@dataclass(frozen=True)
class Config:
    scoring: ModelConfig
    reading_model: ModelConfig
    paths: PathConfig
    agent: AgentConfig
    fields: tuple[FieldConfig, ...]
    sources: SourcesConfig
    ranking: RankingConfig
    reading: ReadingConfig


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
    agent = _table(data, "agent", "agent")
    reading = _table(data, "reading", "reading")
    return Config(
        scoring=_model(models, "scoring"),
        reading_model=_model(models, "reading"),
        paths=PathConfig(
            run_dir=_path(paths, "run_dir", "paths"),
            note_dir=_path(paths, "note_dir", "paths"),
            parent_note=_path(paths, "parent_note", "paths"),
        ),
        agent=AgentConfig(
            command=_command(agent),
            language=_value(agent, "language", str, "agent"),
        ),
        fields=_fields(data),
        sources=_sources(data),
        ranking=_ranking(data),
        reading=ReadingConfig(
            max_items=_positive(reading, "max_items", "reading"),
            max_chars=_positive(reading, "max_chars", "reading"),
        ),
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
    return ModelConfig(
        base_url=_value(table, "base_url", str, context),
        model=_value(table, "model", str, context),
        max_concurrency=_positive(table, "max_concurrency", context),
    )


def _command(agent: Mapping[str, Any]) -> tuple[str, ...]:
    command = _strings(agent, "command", "agent")
    if not command:
        raise ConfigError("agent.command must not be empty")
    return command


def _fields(data: Mapping[str, Any]) -> tuple[FieldConfig, ...]:
    tables = data.get("fields")
    if not isinstance(tables, list) or not tables:
        raise ConfigError("fields must contain at least one [[fields]] table")

    fields = []
    for index, table in enumerate(tables):
        context = f"fields[{index}]"
        if not isinstance(table, dict):
            raise ConfigError(f"{context} must be a table")
        quota = _value(table, "quota", int, context)
        if quota < 0:
            raise ConfigError(f"{context}.quota must not be negative")
        fields.append(
            FieldConfig(
                name=_value(table, "name", str, context),
                arxiv_categories=_strings(table, "arxiv_categories", context),
                quota=quota,
            )
        )
    return tuple(fields)


def _sources(data: Mapping[str, Any]) -> SourcesConfig:
    table = _table(data, "sources", "sources")
    context = "sources"
    return SourcesConfig(
        attention_window_days=_positive(table, "attention_window_days", context),
        arrival_grace_days=_positive(table, "arrival_grace_days", context),
        exclude_keywords=_strings(table, "exclude_keywords", context),
        preprint_servers=_strings(table, "preprint_servers", context),
        preprint_categories=_strings(table, "preprint_categories", context),
        preprint_keywords=_strings(table, "preprint_keywords", context),
        hf_model_limit=_positive(table, "hf_model_limit", context),
        hn_min_points=_positive(table, "hn_min_points", context),
        github_topics=_strings(table, "github_topics", context),
        github_limit=_positive(table, "github_limit", context),
        blogs=_strings(table, "blogs", context),
    )


def _ranking(data: Mapping[str, Any]) -> RankingConfig:
    table = _table(data, "ranking", "ranking")
    context = "ranking"
    weights = _table(table, "signal_weights", "ranking.signal_weights")
    for name, weight in weights.items():
        if type(weight) not in (int, float):
            raise ConfigError(f"ranking.signal_weights.{name} must be a number")
    kind_slots = _table(table, "kind_slots", "ranking.kind_slots")
    return RankingConfig(
        signal_weights={name: float(weight) for name, weight in weights.items()},
        kind_slots={kind: _positive(kind_slots, kind, "ranking.kind_slots") for kind in kind_slots},
        min_attention=_number(table, "min_attention", context),
        attention_weight=_number(table, "attention_weight", context),
        content_weight=_number(table, "content_weight", context),
        card_count=_positive(table, "card_count", context),
        global_slots=_positive(table, "global_slots", context),
        novelty_slots=_positive(table, "novelty_slots", context),
    )


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


def _path(table: Mapping[str, Any], key: str, context: str) -> Path:
    return Path(_value(table, key, str, context)).expanduser()


def _positive(table: Mapping[str, Any], key: str, context: str) -> int:
    value = _value(table, key, int, context)
    if value < 1:
        raise ConfigError(f"{context}.{key} must be at least 1")
    return value


def _number(table: Mapping[str, Any], key: str, context: str) -> float:
    if key not in table:
        raise ConfigError(f"missing required setting: {context}.{key}")
    value = table[key]
    if type(value) not in (int, float):
        raise ConfigError(f"{context}.{key} must be a number")
    return float(value)


def _strings(table: Mapping[str, Any], key: str, context: str) -> tuple[str, ...]:
    values = _value(table, key, list, context)
    if not all(isinstance(value, str) for value in values):
        raise ConfigError(f"{context}.{key} must contain only strings")
    return tuple(values)
