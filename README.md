# research-digest

A weekly digest of new research and model releases. Scripts and local models do
the filtering and reading; an agent only chooses what to read and writes the
final note.

The pipeline is under construction. Only configuration commands exist so far.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
mkdir -p ~/.config/research-digest
cp config.example.toml ~/.config/research-digest/config.toml
```

## Configuration

The config file is `~/.config/research-digest/config.toml` on every platform.
An absolute `XDG_CONFIG_HOME` replaces `~/.config`, and `--config` selects a
specific file.

API keys for external services go in an `env` file beside it, as `KEY=VALUE`
lines, readable only by you (`chmod 600`). Variables already set in the
environment take priority. Local models need no key.

## Commands

```sh
uv run research-digest config path
uv run research-digest config check
```
