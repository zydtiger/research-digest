"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ConfigError, default_config_path, load_config, load_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-digest", description=__doc__)
    parser.add_argument("--config", type=Path, help="config file instead of the default path")
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config", help="inspect configuration")
    actions = config.add_subparsers(dest="action", required=True)
    actions.add_parser("path", help="print the config file path in use")
    actions.add_parser("check", help="validate the config file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = default_config_path() if args.config is None else args.config

    if args.action == "path":
        print(config_path)
        return 0

    try:
        load_env()
        load_config(config_path)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"ok: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
