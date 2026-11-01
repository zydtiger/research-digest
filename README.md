# research-digest

A weekly digest of new research and model releases. Scripts and local models do
the filtering and reading; an agent only chooses what to read and writes the
final note.

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), the
[`papers` CLI](https://github.com/zydtiger/papers-cli), an
agent command that reads a prompt on stdin and prints its reply on stdout, and a
running OpenAI-compatible endpoint for the local model.

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
environment take priority. Local models need no key. `GITHUB_TOKEN` raises the
GitHub search rate limit.

## Commands

```sh
uv run research-digest config path
uv run research-digest config check
uv run research-digest run
```

`run` resumes this ISO week's run or starts a new one covering the time since the
last finished run. Useful options:

```sh
uv run research-digest run --dry-run
uv run research-digest run --since 2026-10-24 --until 2026-10-31
uv run research-digest run --stage rank
uv run research-digest run --model MODEL --base-url URL
```

`--dry-run` stops before the agent. The last line of a successful run is a JSON
object with a summary and the note's path.

## License

[MIT](LICENSE)
