#!/usr/bin/env python3
"""Enable a curated set of common Wave services in the registry.

Run after discovery to turn on the everyday services. Users can enable their own
product repos afterward with set_codebase_enabled / the MCP tool.

Usage:
    poetry run python scripts/seed_default_repos.py
"""
from __future__ import annotations

import logging
import os
import sys
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager

log = logging.getLogger(__name__)

DEFAULT_REPOS: List[str] = [
    "identity",
    "reef",
    "api",
    "javascript-wave-api-client",
    "next-wave",
    "wave-messages",
    "lighthouse",
    "chunnelx",
    "nav",
    "tuktuk",
    "buoyant",
]


def seed_defaults(db: DatabaseManager) -> List[str]:
    """Enable each DEFAULT_REPOS entry that exists in the registry.

    Returns the list of names actually enabled (those present in the registry).
    """
    enabled: List[str] = []
    for name in DEFAULT_REPOS:
        if db.set_codebase_enabled(name, True):
            enabled.append(name)
            log.info("Enabled '%s'", name)
        else:
            log.warning("Skipped '%s' — not in registry (run discover_repos first)", name)
    return enabled


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    db = DatabaseManager()
    db.init_schema()
    try:
        enabled = seed_defaults(db)
        print(f"Enabled {len(enabled)} default repos: {', '.join(enabled) or '(none)'}")
        missing = [r for r in DEFAULT_REPOS if r not in enabled]
        if missing:
            print(f"Not found in registry (run discover_repos.py first): {', '.join(missing)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
