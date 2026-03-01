# AGENTS.md (Project Supplements)

This file is a project-level supplement. It does not replace global policy.

## Inheritance Model (Mandatory)

1. Global baseline policy: `C:\Users\devl\.codex\AGENTS.md`.
2. This project file adds stricter project-specific rules only.
3. If rules conflict, the stricter rule wins.

## Project Workflow Additions

Use the full flow for non-trivial tasks:
1) product -> 2) architect -> 3) developer -> 4) reviewer -> 5) qa

Security and devops are mandatory when security/deployment/runtime behavior is touched.

## Commit Control (Mandatory)

1. Agents must never run `git add`, `git commit`, `git push`, `git tag`, or history-rewrite commands without explicit user approval for the current diff.
2. Agents must still prepare a ready-to-use commit message text after each completed task.
3. If approval was not provided, final status must say: `Commit pending user approval`.

## Spec And Docs Lifecycle (Mandatory)

For any functionality/API/behavior/config change, including bug fixes:
1. Update `SPEC.md` (user-facing requirements and acceptance criteria).
2. Update or create design spec in `docs/design/<feature>-vN.md`.
3. Update `docs/REQUIREMENTS_TRACEABILITY.md` (implementation and verification mapping).
4. Update user/ops docs when relevant (`README.md`, `docs/API_REFERENCE.md`, `docs/CONFIGURATION.md`, `docs/USAGE.md`, `docs/OPERATIONS.md`).

If no doc/spec update is required, provide explicit reason in final output.

## Secrets And Data Safety (Mandatory)

1. Secrets must never be committed: tokens, passwords, keys, credentials, private certificates, `.env` contents, or production URLs with credentials.
2. Before completion, run project secret checks and report result.
3. If a secret leak is detected, task is blocked until removed/rotated and history impact is assessed.
4. Sensitive data must not be sent to external services/tools unless user explicitly approved it.

## Clarification-First Behavior (Mandatory)

1. If request is ambiguous or has multiple materially different outcomes, ask clarifying questions before implementation.
2. Developer/planner/reviewer escalate to architect; architect asks user.
3. Final output includes `Clarifications` with: questions, answers, and resolved assumptions.

## Project Skills

- `architect` - create/update design spec before implementation.
- `developer` - implement approved spec with verification and doc updates.
- `reviewer` - review against spec, security, tests, docs.
- `spec-maintainer` - keep `SPEC.md` + design + traceability in sync.
- `cpp-reviewer` - focused C/C++ review for safety/performance/correctness.
- `regression-guard` - map acceptance criteria to regression coverage.
- `token-optimizer` - build minimal context packs to reduce token usage.
