# Design: Wiki URL Crawl State Fix v2 (Gitee recursive sync hardening)

Date: 2026-03-06  
Type: Bugfix hardening (wiki ingestion completeness)

## Context
- v1 restored missing bot state handling (`waiting_wiki_root`) so wiki crawl is triggered correctly.
- Runtime logs still showed incomplete sync: only root page was ingested for Gitee wiki URLs (`pages=1`, `chunks=1`).

## Root Cause
- Gitee wiki pages are largely JS-rendered; static HTML has almost no recursive wiki links in `<a href>`.
- Existing HTML crawler in `shared/wiki_scraper.py` relies on anchor traversal, so recursion scope is often limited to root page only.

## Solution
- Add host-aware fallback in `crawl_wiki_to_kb`:
  - if URL belongs to Gitee wiki (`*.gitee.com` + `/wikis`), prefer `shared.wiki_git_loader.load_wiki_from_git` for full content sync;
  - map `files_processed` -> `pages_processed` in returned stats to preserve API contract expectations;
  - if git fallback fails, continue with existing HTML crawl path as safe fallback.

## Scope
- Runtime code: `shared/wiki_scraper.py`.
- Regression tests: `tests/test_wiki_scraper.py`.
- Spec/traceability updates for behavior contract.
- No DB schema/API endpoint shape changes.

## Verification
- `.venv\Scripts\python.exe -m pytest -q tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py -k wiki`
- `.venv\Scripts\python.exe -m pytest -q tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py`
- `python -m py_compile shared/wiki_scraper.py tests/test_wiki_scraper.py`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`

## Risks and Mitigations
- Risk: git loader failure (network/auth/git issue) could break wiki-crawl for Gitee.
  - Mitigation: fallback to previous HTML crawl path with warning logs.
- Risk: behavior divergence between generic wiki sites and Gitee.
  - Mitigation: fallback is host-scoped (`gitee.com` + `/wikis`), other hosts keep existing generic crawler.

## Rollback
- Remove Gitee fallback block from `shared/wiki_scraper.py` and keep pure HTML crawl logic.
- Keep/adjust tests accordingly.
