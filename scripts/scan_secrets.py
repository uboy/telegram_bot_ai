#!/usr/bin/env python3
"""Simple secret scanner for tracked repository files."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|DSA|PRIVATE) KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|private[_-]?key)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-+/=]{8,})"),
]
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ALLOW_HINTS = (
    "example",
    "sample",
    "dummy",
    "changeme",
    "your_",
    "test-token",
    "<",
    ">",
)

EXCLUDE_FILES = {
    ".env",
    "env.template",
    ".gitignore",
}

EXCLUDE_PREFIXES = (
    ".git/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".codex/",
)


def run_git_ls_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git ls-files failed")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def should_skip(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in EXCLUDE_FILES:
        return True
    if normalized.startswith(EXCLUDE_PREFIXES):
        return True
    return False


def is_binary(data: bytes) -> bool:
    return b"\x00" in data


def line_is_allowlisted(line: str) -> bool:
    low = line.lower()
    return any(hint in low for hint in ALLOW_HINTS)


def scan_file(path: str) -> list[str]:
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except OSError:
        return []
    if is_binary(data):
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="ignore")

    findings: list[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line_is_allowlisted(stripped):
            continue
        for idx_pattern, pattern in enumerate(PATTERNS):
            match = pattern.search(stripped)
            if not match:
                continue
            # Ignore identifier assignments like api_key=openai_api_key.
            if idx_pattern == len(PATTERNS) - 1:
                assigned = match.group(1)
                if IDENTIFIER_RE.fullmatch(assigned):
                    continue
                if assigned.lower() in ALLOW_HINTS:
                    continue
            findings.append(f"{path}:{idx}: {stripped[:180]}")
            break
    return findings


def main() -> int:
    argparse.ArgumentParser().parse_args()

    findings: list[str] = []
    for path in run_git_ls_files():
        if should_skip(path):
            continue
        findings.extend(scan_file(path))

    if findings:
        print("secret-scan: potential sensitive data detected", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1

    print("secret-scan: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
