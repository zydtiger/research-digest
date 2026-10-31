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

## 2026-10-31: configuration lives outside the repository

Config resolves from `--config`, then an absolute `XDG_CONFIG_HOME`, then
`~/.config` on every platform. Rejected: repository config and platform-native
directories, so that one path works on macOS and Linux.
