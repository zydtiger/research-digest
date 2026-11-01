"""The item format shared by every pipeline stage."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field


class Scores(BaseModel):
    novelty: int = Field(ge=0, le=10)
    impact: int = Field(ge=0, le=10)
    credibility: int = Field(ge=0, le=10)
    field: str
    reason: str


class Item(BaseModel):
    id: str
    kind: str
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    published: str
    url: str
    repo: str | None = None
    sources: list[str] = Field(default_factory=list)
    signals: dict[str, float] = Field(default_factory=dict)
    arxiv_refs: list[str] = Field(default_factory=list)
    scores: Scores | None = None
    attention: float = 0.0
    rank_score: float = 0.0
    lane: str = ""


def merge(target: Item, other: Item) -> None:
    """Fold another record of the same result into the target."""
    if len(other.abstract) > len(target.abstract):
        target.abstract = other.abstract
    target.authors = target.authors or other.authors
    # Keep order: an arXiv record lists its primary category first.
    target.categories = list(dict.fromkeys([*target.categories, *other.categories]))
    target.repo = target.repo or other.repo
    target.sources = sorted({*target.sources, *other.sources})
    target.arxiv_refs = sorted({*target.arxiv_refs, *other.arxiv_refs})
    for name, value in other.signals.items():
        target.signals[name] = max(target.signals.get(name, 0.0), value)


def write_items(path: Path, items: Iterable[Item]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for item in items:
            handle.write(item.model_dump_json() + "\n")
    temporary.replace(path)


def read_items(path: Path) -> list[Item]:
    with path.open() as handle:
        return [Item.model_validate(json.loads(line)) for line in handle if line.strip()]
