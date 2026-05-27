#!/usr/bin/env python3
"""Poll enabled codebases for new commits and reindex on change.

Usage:
    # Check once and exit:
    poetry run python scripts/reindex_if_changed.py --once

    # Run forever, checking every 5 minutes:
    poetry run python scripts/reindex_if_changed.py --interval 300

    # Preview what would be reindexed without running anything:
    poetry run python scripts/reindex_if_changed.py --once --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_remote_sha(path: str, branch: str) -> Optional[str]:
    """Fetch origin and return the remote HEAD SHA, or None on failure."""
    try:
        subprocess.run(
            ["git", "-C", path, "fetch", "origin", branch],
            check=True,
            capture_output=True,
            timeout=30,
        )
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", f"origin/{branch}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception as exc:
        log.warning("git failed for %s: %s", path, exc)
        return None


def run_reindex(codebase: Dict[str, Any], project_root: Path, dry_run: bool) -> bool:
    """Run build_index.py for the given codebase. Returns True on success."""
    script = project_root / "scripts" / "build_index.py"
    cmd = [
        sys.executable,
        str(script),
        "--codebase-path", codebase["path"],
        "--index-name", codebase["name"],
        "--description", codebase.get("description") or "",
        "--language", codebase.get("language") or "mixed",
    ]
    if dry_run:
        log.info("[DRY-RUN] Would run: %s", " ".join(cmd))
        return True
    log.info("Reindexing %s ...", codebase["name"])
    result = subprocess.run(cmd)
    return result.returncode == 0


def poll_once(
    db: DatabaseManager,
    project_root: Path,
    dry_run: bool,
) -> Dict[str, int]:
    """Check all enabled codebases and reindex those with new commits.

    Returns a summary dict: {checked, reindexed, skipped, failed}.
    """
    codebases = db.get_enabled_codebases()
    summary: Dict[str, int] = {"checked": 0, "reindexed": 0, "skipped": 0, "failed": 0}

    for cb in codebases:
        summary["checked"] += 1
        sha = get_remote_sha(cb["path"], cb["main_branch_name"])

        if sha is None:
            log.warning("Could not get remote SHA for %s — skipping", cb["name"])
            summary["failed"] += 1
            continue

        if sha == cb.get("last_indexed_commit"):
            log.debug("%s is up to date (%s)", cb["name"], sha[:8])
            summary["skipped"] += 1
            continue

        prev = (cb.get("last_indexed_commit") or "null")[:8]
        log.info("%s: new commit %s (was %s)", cb["name"], sha[:8], prev)

        success = run_reindex(cb, project_root, dry_run)
        if success:
            if not dry_run:
                db.update_last_indexed_commit(cb["name"], sha)
            summary["reindexed"] += 1
        else:
            log.error("Reindex failed for %s", cb["name"])
            summary["failed"] += 1

    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex codebases with new commits")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Check once then exit")
    mode.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help="Poll continuously every N seconds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be reindexed without running build_index.py",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    db = DatabaseManager()
    db.init_schema()

    try:
        if args.once:
            summary = poll_once(db, _PROJECT_ROOT, dry_run=args.dry_run)
            log.info("Done: %s", summary)
        else:
            while True:
                summary = poll_once(db, _PROJECT_ROOT, dry_run=args.dry_run)
                log.info("Poll complete: %s — sleeping %ds", summary, args.interval)
                time.sleep(args.interval)
    finally:
        db.close()


if __name__ == "__main__":
    main()
