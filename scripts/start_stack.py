"""Smart docker-compose launcher.

Checks configuration before startup:
- if MYSQL_URL is set -> enable mysql profile
- if MYSQL_URL is empty -> start without mysql profile
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Dict, List


def parse_env_file(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not os.path.exists(path):
        return env

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            env[key] = value
    return env


def is_mysql_enabled(env: Dict[str, str]) -> bool:
    return bool((env.get("MYSQL_URL") or "").strip())


def build_compose_command(mysql_enabled: bool, detached: bool = True, build: bool = True) -> List[str]:
    cmd: List[str] = ["docker", "compose"]
    if mysql_enabled:
        cmd.extend(["--profile", "mysql"])
    cmd.append("up")
    if detached:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Start project stack with auto DB profile selection.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("--dry-run", action="store_true", help="Only print selected mode/command.")
    parser.add_argument("--no-detach", action="store_true", help="Run compose in foreground.")
    parser.add_argument("--no-build", action="store_true", help="Skip --build flag.")
    args = parser.parse_args()

    env = parse_env_file(args.env_file)
    mysql_mode = is_mysql_enabled(env)
    cmd = build_compose_command(
        mysql_enabled=mysql_mode,
        detached=not args.no_detach,
        build=not args.no_build,
    )

    mode = "MySQL profile enabled" if mysql_mode else "SQLite mode (MySQL profile disabled)"
    print(mode)
    print("Command:", " ".join(cmd))

    if args.dry_run:
        return 0

    try:
        completed = subprocess.run(cmd, check=False)
        return completed.returncode
    except FileNotFoundError:
        print("docker command not found in PATH.", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
