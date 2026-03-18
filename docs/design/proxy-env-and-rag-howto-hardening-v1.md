# Proxy Env Support and Broad HOWTO RAG Hardening

Date: 2026-03-18
Status: Draft for approval
Owner: codex (architect phase)

## 1. Summary

This change combines two production-facing fixes:

1. add an explicit outbound proxy contract for runtime containers and clients;
2. harden broad procedural RAG answers so compound HOWTO queries prefer one coherent procedure instead of stitching unrelated narrow pages together;
3. define repo hygiene rules so temporary agent-generated files are excluded from version control while durable workflow artifacts remain tracked.

### Goals
- Support standard proxy environment variables in the runtime stack.
- Make proxy behavior explicit in code and docs instead of relying on implicit library defaults.
- Improve broad HOWTO retrieval/answer composition for queries like `how to build and sync`.
- Keep the RAG fix generic and corpus-agnostic.
- Prevent accidental commits of temporary agent scratch/debug files.

### Non-goals
- No new proxy protocol abstraction beyond standard env variables.
- No new dependency introduction.
- No corpus-specific hardcoding such as `Sync&Build` or open-harmony-only branches.
- No architecture changes to the overall bot/backend split.

## 2. Scope Boundaries

### In scope
- `docker-compose.yml` env propagation for standard proxy variables.
- config normalization for optional proxy envs.
- explicit outbound proxy wiring for:
  - Telegram polling transport,
  - bot -> backend HTTP client,
  - selected runtime `requests` clients that call external services.
- RAG ranking/fallback hardening for broad compound HOWTO queries.
- repo ignore and documentation updates for agent temporary files.
- tests, spec/docs, and operations notes for the new behavior.

### Out of scope
- SOCKS-specific UX or dedicated admin controls for proxy setup.
- TLS certificate custom stores or mTLS.
- changing deployment topology.
- changing ingestion contracts or KB schema.
- deleting existing durable coordination/review artifacts that are intentionally versioned.

## 3. Assumptions and Constraints

- Project policy requires design-first workflow for non-trivial tasks.
- No dependency changes unless explicitly approved.
- Existing architecture/docs are frozen unless changed only as part of the approved design/spec lifecycle.
- Proxy URLs may embed credentials; logs must not leak them.
- Runtime must remain valid for containerized and non-containerized runs.

## 4. Architecture

### 4.1 Proxy configuration path

The project will adopt standard outbound proxy env variables as the single supported contract:

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `ALL_PROXY`
- `NO_PROXY`

These values will be:

1. normalized in shared config helpers;
2. passed into runtime containers through compose/env templates;
3. consumed explicitly by outbound HTTP clients where practical.

### 4.2 Runtime clients

#### Telegram bot transport
- `frontend/bot.py` currently uses the default `ApplicationBuilder`.
- The implementation will construct the Telegram request transport explicitly from normalized proxy envs so the Telegram path does not depend on accidental library defaults.

#### Bot -> backend client
- `frontend/backend_client.py` will use a shared helper to create `httpx.Client` instances with the normalized proxy/trust-env settings.
- Internal service traffic must remain compatible with `NO_PROXY`, especially for `backend`, `qdrant`, `redis`, and localhost-style destinations.

#### Other outbound HTTP clients
- Selected `requests`-based clients used for external endpoints will be aligned to the same env contract.
- The implementation should avoid large client-factory refactors beyond the touched runtime surfaces.

### 4.3 RAG broad HOWTO hardening

The current generalized retrieval already includes metadata-field rescue and procedural tie-break scoring, but the live answer shows the final composition can still over-include narrow versioned build pages.

The design change is:

1. detect compound procedural intent more strongly at the ranking/fallback stage;
2. prefer a coherent anchor set from the same procedural document/section family;
3. expand neighboring context around that anchor before admitting weaker pages from unrelated families;
4. keep fallback source attribution and answer assembly aligned to that narrowed evidence pack.

This is a ranking/context-assembly refinement, not a new retrieval backend.

### 4.4 Agent temporary-file hygiene

The repo needs an explicit separation between:

- durable workflow artifacts that remain tracked;
- ephemeral agent byproducts that must be ignored.

Durable artifacts include:
- approved design docs under `docs/design/`
- traceability/spec/doc updates
- review reports under `coordination/reviews/`
- stable coordination records intentionally used as project history

Ephemeral artifacts include:
- temporary scratch files created during one run
- local debug dumps / ad hoc JSON outputs
- agent temp logs or disposable notes that are not part of the documented workflow contract

Implementation should update ignore rules narrowly so required workflow evidence stays commit-visible.

## 5. Interfaces and Contracts

### 5.1 Configuration contract

New documented runtime contract:

- proxy env variables are optional;
- empty values are treated as unset;
- `NO_PROXY` is supported for internal services and direct routes.

Expected config helper shape:

```python
def get_outbound_proxy_settings() -> dict[str, str | None]:
    ...
```

or equivalent small helpers for `httpx` and Telegram transport wiring.

### 5.2 Internal client factory contract

Expected helper boundary:

```python
def build_httpx_client(*, timeout: float, headers: dict | None = None) -> httpx.Client:
    ...
```

Requirements:
- honor normalized proxy envs;
- preserve current timeouts and headers;
- avoid leaking proxy credentials in logs/exceptions.

### 5.3 RAG route behavior contract

For broad compound HOWTO queries:
- ranking should prefer coherent procedural sources over narrow troubleshooting or version-specific feature pages when both are present;
- provider fallback should expand around the strongest procedural anchor before using a broader mixed result set;
- final sources and answer snippets should reflect that narrowed evidence pack.

No HTTP route schema changes are required.

### 5.4 Repo hygiene contract

Expected implementation outcomes:
- document which agent/workflow files are durable;
- add ignore patterns only for temporary artifacts;
- avoid broad ignore rules that would hide required policy artifacts such as review reports or approved design specs.

## 6. Data Model Changes and Migrations

None expected.

## 7. Edge Cases and Failure Modes

### Proxy
- proxy env set but malformed:
  - client creation must fail clearly and surface an actionable error.
- proxy set globally but internal services should bypass:
  - `NO_PROXY` must cover `backend`, `qdrant`, `redis`, `localhost`, `127.0.0.1`, and Docker-network service names as needed.
- proxy credentials present:
  - logs must redact or avoid printing raw proxy URL values.

### RAG
- compound HOWTO query where no strong procedural anchor exists:
  - system may still use the broader ranked set; the narrowing must be opportunistic, not destructive.
- procedural doc exists but its top chunk alone lacks all steps:
  - fallback/context expansion must include neighboring chunks from the same doc/section family.
- one KB contains many versioned near-duplicates:
  - ranking should prefer the best coherent family, not concatenate all versions into one answer.

### Repo hygiene
- an overbroad `.gitignore` rule could hide required workflow evidence:
  - design docs,
  - review reports,
  - traceability updates,
  - durable coordination records.
- implementation must distinguish scratch/temp paths from required project artifacts.

## 8. Security Requirements

- Keep API-key behavior unchanged.
- Do not log raw proxy URLs with embedded credentials.
- Do not widen RAG context selection in a way that weakens existing safety screening.
- Keep command sanitization and URL trust filters active after the HOWTO changes.
- No new dependencies without approval.
- Ignore rules must not be used to conceal required review/spec artifacts.

## 9. Performance Requirements and Limits

- Proxy wiring must add negligible latency beyond the proxy itself.
- Shared client helpers must not significantly increase client construction overhead relative to current behavior.
- RAG hardening must remain bounded to existing candidate windows and local context expansion limits.
- No unbounded document-family scans in the request path.
- Repo hygiene changes must stay minimal and deterministic.

## 10. Observability

- Log whether proxy support is enabled in a redacted form, for example:
  - proxy enabled: yes/no
  - `NO_PROXY` present: yes/no
- Do not log full proxy URLs.
- Add debug evidence for broad HOWTO narrowing decisions in existing retrieval diagnostics metadata where practical:
  - anchor family chosen,
  - whether fallback narrowing was activated,
  - number of same-family neighbor rows included.

## 11. Test Plan

### Unit / focused regression
- proxy config normalization tests
- HTTP client factory proxy wiring tests
- Telegram transport wiring tests
- broad HOWTO ranking regression for `how to build and sync`
- fallback narrowing regression proving mixed version-specific pages do not beat a stronger procedural family
- ignore-pattern/unit coverage or deterministic checks for temporary-agent-file exclusion rules where practical

### Integration / local smoke
- re-run `tests/test_openharmony_wiki_local_smoke.py` in opt-in local mode
- verify `how to build and sync` returns canonical sync/build evidence and answer text

### Exact commands for implementation phase
- `python -m py_compile <changed_py_files>`
- `.venv\Scripts\python.exe -m pytest -q <focused test files>`
- `.venv\Scripts\python.exe -m pytest -q tests/test_openharmony_wiki_local_smoke.py` with local smoke env
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`

## 12. Rollout and Rollback

### Rollout
- add proxy env documentation and compose propagation first;
- wire clients explicitly;
- land RAG ranking/fallback hardening with focused regressions;
- add narrow ignore/documentation updates for temporary agent files;
- validate with local smoke.

### Rollback
- unset proxy env variables to restore direct networking behavior;
- revert the client-wiring and RAG hardening diff if regressions appear;
- revert ignore-rule/doc changes if they hide required artifacts;
- no data migration rollback is needed.

Expected recovery time: minutes.
Data loss risk: none expected.

## 13. Acceptance Criteria

- [ ] Runtime containers document and accept `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`.
- [ ] Telegram and bot outbound HTTP clients use the explicit proxy env contract.
- [ ] Internal service calls remain functional with `NO_PROXY` configured.
- [ ] Broad HOWTO query `how to build and sync` prefers a coherent procedural document/section family over narrow version-specific feature pages when both exist.
- [ ] Fallback/answer assembly does not stitch together unrelated versioned pages when a stronger procedural anchor exists.
- [ ] Temporary agent-generated files are excluded by narrow repo ignore rules without hiding required durable workflow artifacts.
- [ ] Focused regressions and local smoke verification pass.
- [ ] Spec/config/ops docs are updated.

## 14. Spec and Doc Update Plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/CONFIGURATION.md`
- `docs/OPERATIONS.md`
- `README.md`
- `.gitignore` or equivalent repo ignore file, if needed for temporary agent artifacts

If implementation changes the effective user-visible KB behavior, `docs/USAGE.md` should also be updated.

## 15. Secret-Safety Impact

Secrets may appear in:
- proxy URLs with embedded credentials,
- existing API keys in runtime env.

Leak prevention requirements:
- never print raw proxy URLs in logs or error messages when avoidable;
- redact credential-bearing values in diagnostics;
- keep secret scan in the verification set.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
