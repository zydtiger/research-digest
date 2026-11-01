# Status

Updated 2026-10-31.

| Area | Current state | Evidence |
| --- | --- | --- |
| Configuration | Path resolution, validation, and env loading work | [tests](../tests/test_config.py) |
| Pipeline | All six stages ran on 2026-10-24 to 2026-10-31: 4,781 items scored, 10 read, 76 of 80 quotations verified, note written to a test directory | [tests](../tests/test_pipeline.py) |
| Cost and duration | Collection and scoring take about 20 minutes. The agent used about 46k input tokens, mostly the summaries in the write stage | Same run |
| Deployment | Installed as a weekly scheduled task. A one-day run in a scheduler-like environment passed; the scheduler has not triggered a run yet | 2026-10-31 run |

## Known gaps

- bioRxiv and medRxiv papers with the `10.64898` DOI prefix cannot be downloaded
  through `papers`; they are reported as unreadable.
- Anthropic, Meta, xAI, and DeepSeek publish no usable feed, so their posts are
  not collected.
- Attention history is unavailable, so ranking cannot be backtested. It can only
  be judged forward, from later weeks.
- Scores run high: the mean is about 5 and not the requested 2 to 4. Only their
  order is used.

## Undecided

- Docling was not compared with PyMuPDF4LLM. Quotations verify at 95% with the
  current parser, so a comparison is not needed unless summaries show parsing
  errors.
