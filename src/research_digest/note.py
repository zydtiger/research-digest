"""Write the weekly Obsidian note."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from .reading import Reading


def covered_week(until: date) -> str:
    """Return the ISO week a run covers; its end date may fall early in the next week."""
    year, week, _ = (until - timedelta(days=1)).isocalendar()
    return f"{year}-W{week:02d}"


def note_name(until: date) -> str:
    return f"{covered_week(until)} Research Digest"


def render_note(
    until: date,
    body: str,
    readings: Sequence[Reading],
    parent_note: Path,
) -> str:
    reported = sorted(reading.id for reading in readings)
    lines = [
        "---",
        f'up: "[[{parent_note.stem}]]"',
        "tags:",
        "  - review/research-digest",
        f"week: {covered_week(until)}",
        f"reported: {json.dumps(reported)}",
        "---",
        "",
        body.strip(),
        "",
        "## Read in depth",
        "",
        "| Item | Reference | Verified claims |",
        "| --- | --- | --- |",
    ]
    for reading in sorted(readings, key=lambda reading: reading.title):
        verified = sum(claim.verified for claim in reading.claims)
        title = reading.title.replace("|", "\\|")
        checked = f"{verified}/{len(reading.claims)}"
        lines.append(f"| [{title}]({reading.url}) | `{reading.id}` | {checked} |")
    return "\n".join(lines) + "\n"


def write_note(
    note_dir: Path,
    parent_note: Path,
    until: date,
    content: str,
    previous: Path | None = None,
) -> Path:
    """Write the note and link it from its parent, which must already exist.

    Only the run's own previous note is replaced; another run in the same week gets a new name.
    """
    if not parent_note.is_file():
        raise FileNotFoundError(f"parent note not found: {parent_note}")
    note_dir.mkdir(parents=True, exist_ok=True)
    name = note_name(until)
    suffix = 1
    while (path := note_dir / f"{name}.md").exists() and path != previous:
        suffix += 1
        name = f"{note_name(until)} {suffix}"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content)
    temporary.replace(path)

    link = f"[[{name}]]"
    parent = parent_note.read_text()
    if link not in parent:
        parent_note.write_text(f"{parent.rstrip()}\n- {link}\n")
    return path
