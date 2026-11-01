"""Merge duplicate records and apply rule-based filters."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from .config import SourcesConfig
from .items import Item, merge


def normalize(items: Iterable[Item]) -> list[Item]:
    """Merge records by identifier, then fold repositories and models into their papers."""
    merged: dict[str, Item] = {}
    for item in items:
        if item.id in merged:
            merge(merged[item.id], item)
        else:
            merged[item.id] = item.model_copy(deep=True)

    repo_owners = {
        item.repo: item for item in merged.values() if item.kind == "paper" and item.repo
    }
    for item in list(merged.values()):
        owner = None
        if item.kind == "repo" and item.repo in repo_owners:
            owner = repo_owners[item.repo]
        elif item.kind == "model":
            owner = next((merged[ref] for ref in item.arxiv_refs if ref in merged), None)
        if owner is not None:
            owner.signals = {**item.signals, **owner.signals}
            owner.sources = sorted({*owner.sources, *item.sources})
            del merged[item.id]
    return list(merged.values())


def reported_ids(note_dir: Path) -> set[str]:
    """Read the identifiers recorded in the frontmatter of earlier notes."""
    reported: set[str] = set()
    if not note_dir.is_dir():
        return reported
    for path in note_dir.glob("*.md"):
        reported.update(_frontmatter(path.read_text()).get("reported") or [])
    return reported


def _frontmatter(text: str) -> dict[str, Any]:
    """Parse a note's properties in whichever YAML layout Obsidian last saved."""
    _, opening, rest = text.partition("---\n")
    block, closing, _ = rest.partition("\n---")
    if not opening or not closing:
        return {}
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def prefilter(items: Iterable[Item], sources: SourcesConfig, reported: set[str]) -> list[Item]:
    excluded = [keyword.lower() for keyword in sources.exclude_keywords]
    kept = []
    for item in items:
        text = f"{item.title} {item.abstract}".lower()
        if item.id not in reported and not any(keyword in text for keyword in excluded):
            kept.append(item)
    return kept
