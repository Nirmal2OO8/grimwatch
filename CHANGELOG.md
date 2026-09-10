# Changelog

## 1.0.0

- Renamed the configuration override environment variable from
  `REELQ_CONFIG_DIR` to `GRIMWATCH_CONFIG_DIR`.
- Classification metadata verification uses an expiring local cache and
  non-fatal source fallthrough, so a temporary failure at one provider does not
  automatically make an entire batch unclassifiable.
- Added optional TMDb and OMDb configuration keys as later fallback metadata
  sources.
- `classify` surfaces a confidence indicator and possible runner-up genre so
  uncertain decisions are visible for review.
- Added a rotating `~/.grimwatch/grimwatch.log` for metadata and Groq provider
  exceptions.

## 0.3.2

- Fixed `classify` marking most or all of a batch `UNCATEGORIZED IMPORTS` when
  GPT-OSS returned a partial JSON array. Groq's strict structured-output mode
  used constrained decoding against a schema that required an exact record
  count (`minItems == maxItems`); when the model ran out of completion budget
  before finishing the array, Groq rejected the whole response with a 400
  `json_validate_failed` before the app ever saw it, and the entire batch was
  marked unclassified in one shot. The schema no longer requires an exact
  count, batches are smaller (16 → 8), the completion budget now scales with
  batch size instead of a flat constant, and unresolved titles are retried in
  progressively smaller groups (8 → 4 → 1) instead of failing outright.
- `classify --write` no longer writes or overwrites the companion file when
  any title remains unresolved after every retry; it prints the specific
  titles and requires `--force` to write anyway with those left under
  `UNCATEGORIZED IMPORTS`.
- Added bounded backoff/retry for rate-limited (429) Groq requests instead of
  failing immediately.
- Provider error messages shown in the classify table are now trimmed instead
  of dumping the full raw `failed_generation` payload into the WHY column.
- Removed a slur that had been hardcoded into the mood-matching synonym list.

## 0.3.1

- Prevented repeated `tonight` and `suggest` picks by persisting a per-archive
  cooldown for recently displayed recommendations.
- Constrained explicit mood prompts to matching local shelves before AI
  reranking, so unrelated films cannot slip through on syntactically valid AI
  output.
- Rebuilt classification batching around compact prompts, strict GPT-OSS JSON
  schemas, and retry-only-missing logic for partial provider responses.

## 0.3.0

- Split archive parsing from import parsing, so raw Markdown title lists merge
  correctly instead of being mistaken for genre headings.
- Centralized Unicode-safe title normalization, duplicate comparison, and
  Windows path/encoding handling across add, merge, dedup, and recommendations.
- Rebuilt AI recommendation validation around local candidate IDs, tolerant
  response parsing, and verified local fallbacks.
- Made classification source-file-only; it writes optional companion files and
  refuses the configured archive as a classification input.
- Added dry-run deduplication, a normal `--help` command, and a legacy code-page
  output fallback.

## 0.2.0

- Reworked the terminal UI with a cohesive 8-bit gothic palette, wordmark,
  responsive stat cards, clearer shelves, and useful empty states.
- Fixed optional-AI setup so skipping a key does not reopen setup on every run.
- Hardened markdown parsing, title matching, duplicate detection, atomic saves,
  and validation of AI responses.
- Added genre-aware search and safer batch imports from markdown files.

## 0.1.0

First release.

- `grimwatch add` — single title or batch `.txt` import with AI classification into 56 genre categories
- `grimwatch watch` / `grimwatch watch --undo` — mark films watched with fuzzy title matching
- `grimwatch suggest` — mood, similar, and contrast recommendation modes
- `grimwatch tonight` — single pick, optional runtime filter
- `grimwatch search` — fuzzy search across the full list
- `grimwatch list` — browse by genre with watched/unwatched filtering
- `grimwatch stats` — progress bars, decade heatmap, AI blind spot analysis
- `grimwatch classify --audit` — interactive misclassification review
- `grimwatch merge` — merge two lists with conflict resolution
- `grimwatch dedup` — find and remove duplicates
- `grimwatch setup` — first-run wizard
