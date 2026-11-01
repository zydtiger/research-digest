# Architecture

## Flow

Each stage reads the previous stage's files in the run directory, so a failed run
resumes where it stopped.

| Stage | Work | Output |
| --- | --- | --- |
| collect | Fetch arXiv, bioRxiv, medRxiv, Hugging Face daily papers and trending models, Hacker News, GitHub, and lab blogs. Merge the paper, repository, and discussion of one result, then drop reported and excluded items. | `items.jsonl` |
| score | Drop items that have neither fresh dates nor sustained attention. A local model rates the rest from metadata and abstract. | `scored.jsonl` |
| rank | Assign lanes, compute rank scores, and fill the shortlist. | `ranked.jsonl`, `cards.json` |
| select | The agent reads only the cards and picks the items to read. | `selection.json` |
| read | Download through `papers`, parse, summarize locally with quotations, and check each quotation against the source text. | `texts/`, `readings.json` |
| write | The agent reads the summaries and returns the note body; the program writes the note and links it from the parent note. | Weekly note |

## Ranking

Attention is the weighted sum of source signals divided by the square root of age
in days. An item at or above `min_attention` is in the attention lane; any other
item dated inside the window is in the novelty lane; the rest are dropped.

The shortlist takes the top attention items, then each field's quota, then each
kind's slots, then the novelty slots, and fills what remains from attention and
then novelty.

## Invariants

- The agent is a configured command that receives data on stdin and prints its
  reply. The program validates the reply and does every write.
- The model endpoint is expected to be running; the program never starts or stops it.
- Local-model failure stops the run; the agent does not take over reading.
- One failed source does not stop the run; the note states the gap.
- An unreadable selected item does not stop the run; the note states it.
- Fetched content is untrusted data in every prompt.
- A claim is verified only when its quotation occurs in the parsed source.

## State

| Data | Owner |
| --- | --- |
| Conclusions, summaries, and reading table | Obsidian weekly note |
| Item identifiers already reported | Weekly note frontmatter |
| Downloaded full text | `papers` |
| Candidates, scores, and resume state | Run directory |
