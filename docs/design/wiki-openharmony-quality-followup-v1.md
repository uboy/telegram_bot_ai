# Wiki URL and OpenHarmony Quality Follow-up v1

Date: 2026-03-10
Status: approved for implementation
Task: `BOTFOLLOW-003`

## Goal

Close the two live issues found during real bot usage:
- entering wiki-by-URL mode must not be contaminated by stale document-upload state;
- default retrieval quality for wiki-style OpenHarmony markdown content must improve enough that build/sync questions hit the right sections more reliably.

## Scope

1. Wiki URL flow isolation
- entering `kb_wiki_crawl:<id>` clears stale document-upload keys (`kb_id`, `upload_mode`, pending document payloads);
- finishing `waiting_wiki_root` clears the same upload keys so the bot does not fall back into document-upload behavior on the next step.

2. OpenHarmony quality improvement
- default KB chunking for `wiki` and `markdown` switches from coarse `full` mode to section-aware chunking;
- ZIP wiki imports normalize temp-derived titles so source labels and section titles use stable page names instead of `tmp...`.

## Why this change

The live Telegram trace showed two practical problems:
- wiki URL entry did not visibly complete through the expected wiki-crawl response path;
- search answers over the imported OpenHarmony corpus were often driven by coarse, one-fragment-per-page chunks and temp-derived titles, which hurt both relevance and source readability.

Local-only comparison against the OpenHarmony wiki ZIP showed a strong quality signal:
- `full` chunking produced about `99` chunks and poor build/sync relevance;
- `section` chunking produced about `768` chunks and materially better source selection for build/sync/mirror queries.

## Test plan

- bot callback regression: `kb_wiki_crawl` clears stale upload/document keys;
- bot text-state regression: `waiting_wiki_root` completion clears stale upload/document keys;
- wiki ZIP loader regression: temp-derived titles are replaced by stable page titles in metadata and chunk title;
- KB settings regression: default `wiki` / `markdown` chunking mode is `section`;
- local-only OpenHarmony ingest/query comparison remains developer-local and uncommitted.
