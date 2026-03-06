# Design: RAG Quality Metrics Contract v1 (P0-1)

Date: 2026-03-06  
Step: `RAGQLTY-001` / `P0-1`

## Purpose
- Define a stable, domain-agnostic quality contract for RAG evaluation and gate decisions.
- Ensure all further quality improvements are measured against the same criteria.

## Scope
- Metrics definitions and acceptance thresholds.
- Required slices for quality gate.
- Baseline delta policy and bootstrap CI rule.

## Out of Scope
- Changes to retrieval/prompt/safety logic.
- Dataset restructuring (covered by `P0-2`).

## Metrics Contract

### Retrieval metrics
- `recall_at_10`: at least one relevant evidence chunk is present in top-10 retrieval results.
- `mrr_at_10`: reciprocal rank of first relevant evidence in top-10.
- `ndcg_at_10`: ranking quality with stronger reward for higher-ranked relevant evidence.

These metrics are computed per test case and then aggregated per slice.

### Relevance signal
Relevant evidence is considered found if either is true:
- `expected_source` is matched in retrieved `source_path`, or
- at least one `expected_snippets` entry is found in retrieved chunk content.

## Required Slices
Mandatory slices for gate checks:
- `ru`, `en`, `mixed`, `factoid`, `howto`, `legal`, `numeric`, `long-context`.

`overall` slice is collected for reporting but is not used as a sole gate substitute for required slices.

## Thresholds (Default)
- `recall_at_10 >= 0.60`
- `mrr_at_10 >= 0.45`
- `ndcg_at_10 >= 0.50`

## Statistical Gate Rules
- Minimum sample size per `(slice, metric)`: `>= 100`.
- Baseline is mandatory by default (`baseline_run_id` required).
- Delta rule: run metric must be non-negative vs baseline (`delta >= 0`).
- Bootstrap CI rule: 95% CI lower bound of delta must not cross negative margin (`ci_low >= -0.01`).
- If bootstrap samples are missing and no explicit override is set, gate fails.

## Pass/Fail Semantics
A run passes only if all checks pass for all required slices and all required metrics.
Any of the following fails the run:
- missing metric row,
- sample size below minimum,
- threshold not met,
- missing baseline row,
- negative delta vs baseline,
- missing bootstrap samples (when strict mode enabled),
- CI crossing configured negative margin.

## Implementation Mapping (current)
- Gate script: `scripts/rag_eval_quality_gate.py`.
- Eval execution and metric persistence: `backend/services/rag_eval_service.py`.
- Regression corpus: `tests/rag_eval.yaml`.
- Gate unit tests: `tests/test_rag_eval_quality_gate.py`.

## Operational Notes
- Thresholds are configurable via CLI flags in gate script, but values above are canonical defaults for CI.
- Any threshold change requires:
  - design update,
  - traceability update,
  - review artifact with rationale and baseline impact.

## Verification for P0-1
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`

No runtime code changes in this step.
