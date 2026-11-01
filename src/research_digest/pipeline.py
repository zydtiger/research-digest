"""Run the pipeline stages and record their state in a run directory."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import agent as agent_module
from . import note as note_module
from .config import Config, ModelConfig
from .filtering import normalize, prefilter, reported_ids
from .items import read_items as load_items
from .items import write_items
from .llm import ModelUnavailable, check_available
from .ranking import card, lane_of, rank_items, score_items, shortlist
from .reading import Reading, read_items
from .sources import collect

STAGES = ("collect", "score", "rank", "select", "read", "write")
LOCAL_STAGES = ("collect", "score", "rank")
DEFAULT_FIRST_WINDOW_DAYS = 7
MAX_SCORE_FAILURE_RATIO = 0.2


class Manifest(BaseModel):
    since: str
    until: str
    completed: list[str] = Field(default_factory=list)
    gaps: dict[str, str] = Field(default_factory=dict)
    read_failures: dict[str, str] = Field(default_factory=dict)
    note: str | None = None


class Run:
    """One run directory and its manifest."""

    def __init__(self, path: Path, manifest: Manifest) -> None:
        self.path = path
        self.manifest = manifest

    @property
    def since(self) -> date:
        return date.fromisoformat(self.manifest.since)

    @property
    def until(self) -> date:
        return date.fromisoformat(self.manifest.until)

    @property
    def finished(self) -> bool:
        return "write" in self.manifest.completed

    def save(self) -> None:
        temporary = self.path / "manifest.json.tmp"
        temporary.write_text(self.manifest.model_dump_json(indent=2))
        temporary.replace(self.path / "manifest.json")

    def mark(self, stage: str) -> None:
        if stage not in self.manifest.completed:
            self.manifest.completed.append(stage)
        self.save()


def load_runs(run_root: Path) -> list[Run]:
    runs = []
    if run_root.is_dir():
        for path in sorted(run_root.iterdir()):
            manifest_path = path / "manifest.json"
            if manifest_path.is_file():
                manifest = Manifest.model_validate_json(manifest_path.read_text())
                runs.append(Run(path, manifest))
    return runs


def choose_run(
    run_root: Path,
    today: date,
    since: date | None,
    until: date | None,
) -> Run:
    """Resume this week's run, or start one after the last finished run."""
    runs = load_runs(run_root)
    if since is None and until is None:
        week = today.isocalendar()[:2]
        for run in reversed(runs):
            if run.until.isocalendar()[:2] == week:
                return run
    finished = [run for run in runs if run.finished]
    end = today if until is None else until
    if since is not None:
        start = since
    elif finished:
        start = max(run.until for run in finished)
    else:
        start = end - timedelta(days=DEFAULT_FIRST_WINDOW_DAYS)
    if start > end:
        raise ValueError(f"since {start} is after until {end}")

    run_root.mkdir(parents=True, exist_ok=True)
    name = end.isoformat()
    suffix = 1
    while (run_root / name).exists():
        suffix += 1
        name = f"{end.isoformat()}-{suffix}"
    path = run_root / name
    path.mkdir()
    run = Run(path, Manifest(since=start.isoformat(), until=end.isoformat()))
    run.save()
    return run


def _fresh_after(run: Run, config: Config) -> date:
    return run.since - timedelta(days=config.sources.arrival_grace_days)


def stage_collect(run: Run, config: Config) -> None:
    items, gaps = collect(config, run.since, run.until)
    run.manifest.gaps = gaps
    normalized = normalize(items)
    filtered = prefilter(normalized, config.sources, reported_ids(config.paths.note_dir))
    write_items(run.path / "items.jsonl", filtered)
    print(f"collect: {len(items)} records, {len(filtered)} items after filtering", flush=True)
    for name, reason in gaps.items():
        print(f"collect: gap in {name}: {reason}", flush=True)


def stage_score(run: Run, config: Config) -> None:
    collected = load_items(run.path / "items.jsonl")
    fresh_after = _fresh_after(run, config)
    items = [
        item
        for item in collected
        if lane_of(item, config.ranking, run.until, fresh_after) is not None
    ]
    print(f"score: {len(items)} of {len(collected)} items are eligible", flush=True)
    check_available(config.scoring)
    scored, failures = score_items(items, config, config.scoring)
    if items and failures / len(items) > MAX_SCORE_FAILURE_RATIO:
        raise ModelUnavailable(f"{failures} of {len(items)} items could not be scored")
    write_items(run.path / "scored.jsonl", scored)
    print(f"score: {len(scored)} scored, {failures} failed", flush=True)


def stage_rank(run: Run, config: Config) -> None:
    scored = load_items(run.path / "scored.jsonl")
    ranked = rank_items(scored, config.ranking, run.until, _fresh_after(run, config))
    write_items(run.path / "ranked.jsonl", ranked)
    chosen = shortlist(ranked, config.fields, config.ranking)
    (run.path / "cards.json").write_text(
        json.dumps([card(item) for item in chosen], ensure_ascii=False, indent=2)
    )
    print(f"rank: {len(chosen)} cards", flush=True)


def stage_select(run: Run, config: Config) -> None:
    cards = json.loads((run.path / "cards.json").read_text())
    selected = agent_module.select(config.agent, cards, config.reading.max_items, run.path)
    (run.path / "selection.json").write_text(json.dumps(selected, indent=2))
    print(f"select: {len(selected)} items", flush=True)


def stage_read(run: Run, config: Config) -> None:
    selected = json.loads((run.path / "selection.json").read_text())
    by_id = {item.id: item for item in load_items(run.path / "ranked.jsonl")}
    items = [by_id[identifier] for identifier in selected]
    check_available(config.reading_model)
    readings, failures = read_items(items, run.path, config, config.reading_model)
    if not readings:
        raise ModelUnavailable(f"no selected item could be read: {failures}")
    run.manifest.read_failures = failures
    (run.path / "readings.json").write_text(
        json.dumps([reading.model_dump() for reading in readings], ensure_ascii=False, indent=2)
    )
    verified = sum(claim.verified for reading in readings for claim in reading.claims)
    total = sum(len(reading.claims) for reading in readings)
    print(
        f"read: {len(readings)} read, {len(failures)} failed, {verified}/{total} claims verified",
        flush=True,
    )


def stage_write(run: Run, config: Config) -> None:
    readings = [
        Reading.model_validate(reading)
        for reading in json.loads((run.path / "readings.json").read_text())
    ]
    cards = json.loads((run.path / "cards.json").read_text())
    payload: dict[str, Any] = {
        "cards": cards,
        "readings": [reading.model_dump() for reading in readings],
        "cards_shown": len(cards),
        "items_read": len(readings),
        "source_gaps": run.manifest.gaps,
        "unreadable_items": run.manifest.read_failures,
    }
    body = agent_module.write_note(config.agent, payload, run.path)
    content = note_module.render_note(run.until, body, readings, config.paths.parent_note)
    path = note_module.write_note(
        config.paths.note_dir,
        config.paths.parent_note,
        run.until,
        content,
        Path(run.manifest.note) if run.manifest.note else None,
    )
    run.manifest.note = str(path)
    print(f"write: {path}", flush=True)


STAGE_FUNCTIONS = {
    "collect": stage_collect,
    "score": stage_score,
    "rank": stage_rank,
    "select": stage_select,
    "read": stage_read,
    "write": stage_write,
}


def execute(
    config: Config,
    today: date,
    since: date | None = None,
    until: date | None = None,
    stage: str | None = None,
    dry_run: bool = False,
) -> Run:
    """Run the remaining stages, or one named stage, in a resumed or new run."""
    run = choose_run(config.paths.run_dir, today, since, until)
    print(f"run: {run.path} ({run.manifest.since} to {run.manifest.until})", flush=True)
    if stage is not None:
        stages = [stage]
    else:
        stages = [name for name in STAGES if name not in run.manifest.completed]
    if dry_run:
        stages = [name for name in stages if name in LOCAL_STAGES]
    for name in stages:
        STAGE_FUNCTIONS[name](run, config)
        run.mark(name)
    return run


def override_model(config: Config, model: str | None, base_url: str | None) -> Config:
    """Apply command-line overrides to both local model roles."""

    def apply(current: ModelConfig) -> ModelConfig:
        return replace(
            current,
            model=model or current.model,
            base_url=base_url or current.base_url,
        )

    return replace(config, scoring=apply(config.scoring), reading_model=apply(config.reading_model))
