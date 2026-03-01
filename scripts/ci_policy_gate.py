#!/usr/bin/env python3
"""Fail CI when functional changes are not reflected in specs/docs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import PurePosixPath


FUNCTIONAL_PREFIXES = (
    "backend/",
    "frontend/",
    "shared/",
    "open-harmony/",
)
FUNCTIONAL_FILES = {
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "env.template",
}

MANDATORY_DOC_FILES = {
    "SPEC.md",
    "docs/REQUIREMENTS_TRACEABILITY.md",
}
MANDATORY_DOC_PREFIXES = ("docs/design/",)


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def get_changed_files(base: str, head: str) -> list[str]:
    out = run_git("diff", "--name-only", f"{base}..{head}")
    files = [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]
    return sorted(set(files))


def get_working_tree_files() -> list[str]:
    out = run_git("status", "--porcelain")
    files: set[str] = set()
    for line in out.splitlines():
        if not line:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[1].strip()
        if path:
            files.add(path.replace("\\", "/"))
    return sorted(files)


def is_functional_change(path: str) -> bool:
    posix = str(PurePosixPath(path))
    if posix in FUNCTIONAL_FILES:
        return True
    return posix.startswith(FUNCTIONAL_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="Base git ref/sha")
    parser.add_argument("--head", help="Head git ref/sha")
    parser.add_argument("--working-tree", action="store_true", help="Check only current uncommitted changes against HEAD")
    args = parser.parse_args()

    if args.working_tree:
        changed = get_working_tree_files()
    else:
        if not args.base or not args.head:
            parser.error("--base and --head are required unless --working-tree is used")
        changed = get_changed_files(args.base, args.head)
    if not changed:
        print("policy-gate: no changed files")
        return 0

    functional = [p for p in changed if is_functional_change(p)]
    if not functional:
        print("policy-gate: no functional files changed")
        return 0

    changed_set = set(changed)
    missing = []
    for required_file in sorted(MANDATORY_DOC_FILES):
        if required_file not in changed_set:
            missing.append(required_file)

    design_changed = any(p.startswith(MANDATORY_DOC_PREFIXES) for p in changed)
    if not design_changed:
        missing.append("docs/design/<feature>-vN.md")

    if missing:
        print("policy-gate: functional changes detected without required spec/doc updates", file=sys.stderr)
        print("changed functional files:", file=sys.stderr)
        for path in functional:
            print(f"  - {path}", file=sys.stderr)
        print("missing required updates:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("policy-gate: PASS (functional changes include required spec/doc updates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
