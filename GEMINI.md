# GEMINI.md

Project agent behavior for Gemini must match Codex/Claude behavior.

Primary policy stack:
1. `C:\Users\devl\.codex\AGENTS.md` (global baseline)
2. `AGENTS.md` (project supplements)
3. `CLAUDE.md` (project architecture/operations context)

Mandatory highlights:
- No `git add` / `git commit` / `git push` without explicit user approval for current diff.
- Any functional/API/behavior/config change (including bugfix) must update:
  - `SPEC.md`
  - relevant `docs/design/<feature>-vN.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
- Run `python scripts/scan_secrets.py` before completion.
- Ask clarifying questions first when request is ambiguous or can produce multiple materially different outcomes.
