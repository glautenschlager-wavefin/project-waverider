#!/usr/bin/env python3
"""One-time seed of the codebase registry from the seven repos in index_wave_repos.sh.

Idempotent — safe to re-run. Auto-detects the GitHub remote URL from each repo's
git config; falls back to None if the path doesn't exist or git fails.

Usage:
    poetry run python scripts/seed_registry.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager

WAVE_SRC = Path("/Users/glautenschlager/wave/src")

REPOS = [
    {
        "name": "identity",
        "description": "Wave identity Python service",
        "language": "python",
        "main_branch_name": "main",
    },
    {
        "name": "reef",
        "description": "Wave reef TypeScript service",
        "language": "typescript",
        "main_branch_name": "main",
    },
    {
        "name": "embedded-payroll",
        "description": "Wave embedded-payroll TypeScript service",
        "language": "typescript",
        "main_branch_name": "main",
    },
    {
        "name": "payroll",
        "description": "Wave payroll Ruby service",
        "language": "ruby",
        "main_branch_name": "main",
    },
    {
        "name": "next-wave",
        "description": "Wave main web application (React/TypeScript)",
        "language": "typescript",
        "main_branch_name": "main",
    },
    {
        "name": "central-risk",
        "description": "Wave central risk service",
        "language": "python",
        "main_branch_name": "main",
    },
    {
        "name": "next-accounting",
        "description": "Wave next-accounting service",
        "language": "python",
        "main_branch_name": "main",
    },
    {
        "name": "accounting",
        "description": "Wave accounting service",
        "language": "python",
        "main_branch_name": "main",
    },
]


def _detect_github_repo(path: str) -> str | None:
    """Return 'org/repo' by parsing the git origin remote URL, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        url = result.stdout.strip()
        # Handles both SSH (git@github.com:org/repo.git) and HTTPS (https://github.com/org/repo.git)
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        return m.group(1) if m else None
    except Exception:
        return None


def seed(db: DatabaseManager) -> None:
    for repo in REPOS:
        path = str(WAVE_SRC / repo["name"])
        if not Path(path).exists():
            print(f"  SKIP (path not found): {path}")
            continue

        github_repo = _detect_github_repo(path)
        rid = db.upsert_codebase_registration(
            name=repo["name"],
            path=path,
            description=repo["description"],
            language=repo["language"],
            github_repo=github_repo,
            main_branch_name=repo["main_branch_name"],
        )
        github_str = github_repo or "(no remote detected)"
        print(f"  OK: {repo['name']} (id={rid}, github={github_str})")


def main() -> None:
    db = DatabaseManager()
    db.init_schema()
    print("Seeding codebase registry ...")
    seed(db)
    db.close()
    print("Done.")


if __name__ == "__main__":
    main()
