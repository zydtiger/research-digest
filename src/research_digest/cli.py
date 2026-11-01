"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .agent import AgentError
from .config import ConfigError, default_config_path, load_config, load_env
from .llm import ModelUnavailable
from .pipeline import STAGES, execute, override_model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-digest", description=__doc__)
    parser.add_argument("--config", type=Path, help="config file instead of the default path")
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config", help="inspect configuration")
    actions = config.add_subparsers(dest="action", required=True)
    actions.add_parser("path", help="print the config file path in use")
    actions.add_parser("check", help="validate the config file")

    run = commands.add_parser("run", help="run or resume this week's digest")
    run.add_argument(
        "--since",
        type=date.fromisoformat,
        help="start a new run from this date, instead of after the last finished run",
    )
    run.add_argument(
        "--until",
        type=date.fromisoformat,
        help="start a new run ending on this date, instead of today",
    )
    run.add_argument("--stage", choices=STAGES, help="run only this stage")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="stop before any agent call or note write",
    )
    run.add_argument("--model", help="override the local model name")
    run.add_argument("--base-url", help="override the local model base URL")
    return parser


def note_result(note: Path) -> dict[str, str]:
    """Describe the note for a scheduler notification, as the final output line."""
    result = {"summary": f"Open {note.stem}"}
    for parent in note.parents:
        if (parent / ".obsidian").is_dir():
            result["vault"] = parent.name
            result["note"] = str(note.relative_to(parent))
            break
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = default_config_path() if args.config is None else args.config

    if args.command == "config" and args.action == "path":
        print(config_path)
        return 0

    try:
        load_env()
        config = load_config(config_path)
        if args.command == "config":
            print(f"ok: {config_path}")
            return 0
        run = execute(
            override_model(config, args.model, args.base_url),
            date.today(),
            since=args.since,
            until=args.until,
            stage=args.stage,
            dry_run=args.dry_run,
        )
        if run.manifest.note is not None:
            print(json.dumps(note_result(Path(run.manifest.note))))
    except (ConfigError, ModelUnavailable, AgentError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
