#!/usr/bin/env python3
"""Discover repos in a GitHub org and register them (disabled) in the registry.

Usage:
    poetry run python scripts/discover_repos.py
    poetry run python scripts/discover_repos.py --org waveaccounting --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider import config, github_discovery
from waverider.database import DatabaseManager

log = logging.getLogger(__name__)


def run_discovery(
    db: DatabaseManager,
    org: str,
    token: str,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Discover org repos and upsert new ones as disabled.

    Existing codebases are left untouched (their enabled flag is preserved).
    Returns {discovered, new, existing}.
    """
    repos = github_discovery.list_org_repos(org, token)
    existing_names = {cb["name"] for cb in db.list_codebases()}

    new = 0
    for repo in repos:
        if repo.name in existing_names:
            continue
        new += 1
        if dry_run:
            log.info("[DRY-RUN] Would register '%s' (%s, disabled)",
                     repo.name, repo.github_repo)
            continue
        db.upsert_codebase_registration(
            name=repo.name,
            path=None,
            description=repo.description,
            language=repo.language,
            github_repo=repo.github_repo,
            main_branch_name=repo.default_branch,
            enabled=False,
        )

    return {
        "discovered": len(repos),
        "new": new,
        "existing": len(repos) - new,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover GitHub org repos")
    parser.add_argument("--org", default=None, help="GitHub org (default: WAVERIDER_GITHUB_ORG)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    org = args.org or config.get_github_org()
    token = config.get_github_token()

    db = DatabaseManager()
    db.init_schema()
    try:
        summary = run_discovery(db, org=org, token=token, dry_run=args.dry_run)
        log.info("Discovery complete: %s", summary)
    finally:
        db.close()


if __name__ == "__main__":
    main()
