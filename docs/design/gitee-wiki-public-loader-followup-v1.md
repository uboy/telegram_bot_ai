# Gitee Public Wiki Loader Follow-up v1

Date: 2026-03-11
Status: approved for implementation
Task: `BOTFOLLOW-004`

## Goal

Fix the remaining live blocker where URL-based Gitee wiki ingest indexes only the root page because the loader tries to clone the wrong repository path and falls back to shallow HTML crawl.

## Scope

1. Public Gitee wiki clone path
- derive and try public wiki-specific git URLs before the main repository URL;
- disable interactive git credential prompts so failures are fast and deterministic in backend jobs.

2. Dense-index isolation for mixed embedding dimensions
- keep dense indices isolated per KB by embedding dimension so a newly re-embedded corpus does not get dropped because other KBs still carry a different embedding size;
- if a query embedding and a KB-local dense index still disagree, degrade only that dense leg to keyword fallback.

3. Generalized ranking follow-up for broad build/sync queries
- add deterministic field-aware scoring over `source_path`, `doc_title`, `section_title`, and `section_path`;
- use that score only as a generalized tie-break/boost so canonical docs such as `Sync&Build` win over narrower feature pages when candidates are otherwise close.

4. Fallback behavior
- keep the existing HTML crawl as the final fallback if all git candidates fail;
- preserve sync-mode diagnostics so live logs still show whether the run used `git` or `html`.

5. Verification
- add focused loader regressions for candidate URL order and non-interactive git env handling;
- add focused regressions for KB-local mixed embedding dimensions and generalized field-aware ranking;
- re-run local-only OpenHarmony ingest/query comparison after the fix to validate that the canonical `Sync&Build` pages become retrievable through the URL ingest path.

## Why this change

The live backend log shows:
- attempted clone target: `https://gitee.com/mazurdenis/open-harmony.git`;
- failure: `fatal: could not read Username for 'https://gitee.com'`;
- fallback result: only one wiki page indexed through HTML crawl.

With only the root page in the KB, broad questions like `how to build and sync` are forced to rank from an incomplete corpus and return over-specific or weakly grounded sources. During local validation the same open-harmony corpus also exposed a second issue: mixed embedding dimensions across KBs could suppress dense retrieval for the active KB, and once that was fixed the remaining broad build/sync gap was clearly a generalized ranking problem rather than an ingest failure.

## Test plan

- unit regression: public wiki git URL candidates are generated in the expected order;
- unit regression: git clone runs with prompt-disabled environment overrides;
- unit regression: loader falls through candidate URLs until one succeeds;
- unit regression: KB-local mixed embedding dimensions do not suppress dense indices for other KBs;
- unit regression: generalized ranking prefers `Sync&Build` when field matches are stronger than narrower feature docs on a broad build+sync query;
- existing wiki scraper regressions remain green;
- local-only OpenHarmony ingest/query smoke after the fix.
