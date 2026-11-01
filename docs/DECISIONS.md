# Decisions

## 2026-10-31: rank by attention, not similarity

Attention and novelty drive selection, because the interest range is broad and the
goal is work with outsized impact. Rejected: embedding retrieval and BM25 against
an interest profile, since similarity cannot judge impact.

## 2026-10-31: no database

Notes hold conclusions, `papers` holds full text, and run directories hold working
files. Rejected: SQLite, since one weekly writer needs no queries beyond note
search. Cost: rejected candidates are not searchable later.

## 2026-10-31: direct API calls, no agent framework

Local stages are fixed batch steps, called through OpenAI-compatible APIs. Rejected:
agent frameworks, since no local step needs to choose its next action.

## 2026-10-31: arXiv query API, not OAI-PMH

Each category is one query on `submittedDate`, which directly selects new papers.
Rejected: OAI-PMH, which answered the largest categories with 503 after three
minutes each, even for a single day.

## 2026-10-31: tool-free agent

The agent returns text that the program validates and writes. Rejected: a
file-writing agent with restricted tools, since fetched text reaches the prompt and
neither step needs the agent to act. Codex cannot disable its shell tool, so it runs
in a read-only sandbox instead.

## 2026-10-31: the agent is a command, not a CLI integration

The config holds one argv that reads a prompt and prints a reply, so runners, models,
and fallback stay outside this repository. Validation lives here because native
schema flags differ by CLI. Rejected: Claude-specific flags and a model
setting in this repository, since switching agents should not touch it.

## 2026-10-31: discount attention by the square root of age

Dividing by age let a day-old post outrank a two-week-old paper still gathering
votes. Dividing by the square root keeps sustained attention competitive.

## 2026-10-31: no Semantic Scholar

Its API returns 429 without a key, and Hugging Face, Hacker News, and GitHub already
supply attention signals. Revisit if a key becomes available.

## 2026-10-31: model endpoints are only configured, never managed

The config names a base URL and model; an unavailable endpoint is reported as an
error and resumes on the next run. Rejected: starting and stopping a systemd service
over SSH, since it couples the digest to how and where the model is hosted.

## 2026-10-31: configuration lives outside the repository

Config resolves from `--config`, then an absolute `XDG_CONFIG_HOME`, then
`~/.config` on every platform. Rejected: repository config and platform-native
directories, so that one path works on macOS and Linux.
