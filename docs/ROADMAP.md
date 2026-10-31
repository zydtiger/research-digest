# Roadmap

Directions in build order. Order is priority.

| Direction | Question | Start when | Decision it produces |
| --- | --- | --- | --- |
| Feasibility | Which attention sources, history, and PDF parser are usable? How fast does the local model prefill abstracts? | Now | Source list, backtest scope, parser, whether a smaller first-pass model is needed |
| Collection | Can every source be pulled incrementally and deduplicated? | Feasibility is settled | Item format and run directory layout |
| Ranking | Do the two lanes and field quotas produce a list worth reading? | A complete 4-week pull works | Scoring prompt and thresholds; stop here if the ranking is not good enough |
| Deep reading | Are parsed text and local summaries accurate? | The user accepts the ranking | Summary schema and evidence check |
| Agent note | Is the final weekly note useful? | 10 sampled summaries show no obvious errors | Note format and agent permissions |
| Scheduling | Does it run unattended? | A manual week succeeds | Scheduled task; failure notification |

Later, only on request: a manual drop-in path for LinkedIn and RedNote items, and
like or skip feedback.
