# Architecture

This is the proposed design; [STATUS.md](STATUS.md) says what exists.

## Flow

1. **Collect:** preprints (arXiv, bioRxiv, medRxiv), attention signals (such as
   Hugging Face, Semantic Scholar, GitHub, Hacker News), popular Hugging Face
   models, and lab blogs. Each run re-reads a 4-week window.
2. **Normalize:** merge the paper, repository, and discussion of one result into
   one item, and drop items already reported.
3. **Prefilter:** apply field and exclusion rules.
4. **Score:** a local model rates new items from metadata and abstract. Attention
   is normalized by age. An attention lane and a smaller new-work lane share the
   final list.
5. **Rank:** combine signals under per-field quotas, producing about 30 cards.
6. **Select:** the agent reads only the cards and picks 8–10 items.
7. **Read:** download with `papers`, parse, and summarize locally with quotations.
8. **Check:** a script verifies every quotation against the source text and flags
   claims it cannot find.
9. **Write:** the agent reads the summaries and writes the weekly Obsidian note.

## Invariants

- The agent never reads full text unless it needs a specific passage to confirm
  a summary.
- Local-model failure stops the run; the agent does not take over reading.
- One failed source does not stop the run; the note states the gap.
- Fetched content is untrusted, and the agent's tools are limited to reading
  candidates, submitting its selection, and writing the note directory.

## State

| Data | Owner |
| --- | --- |
| Conclusions, summaries, and watchlist | Obsidian weekly note |
| Item identifiers already reported | Weekly note frontmatter |
| Downloaded full text | `papers` |
| Candidates, scores, and resume state | Run directory, kept for a few weeks |

Notes record each item's `ref` (arXiv ID or DOI) or URL, not only its title.
