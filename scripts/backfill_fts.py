#!/usr/bin/env python3
"""Backfill the FTS5 full-text index from existing code_snippets rows.

Run this once after upgrading to hybrid search so that BM25 keyword search
works on previously indexed codebases without requiring a full re-index.

Usage:
    python scripts/backfill_fts.py [--db data/waverider.db]
"""

import argparse

from waverider.database import DatabaseManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill FTS5 index from existing snippets.")
    parser.add_argument("--db", default="data/waverider.db", help="Path to SQLite database")
    args = parser.parse_args()

    db = DatabaseManager(db_path=args.db)

    # Ensure the FTS5 virtual table exists.
    db.init_schema()

    codebases = db.list_codebases()
    if not codebases:
        print("No codebases found in the database.")
        return

    total = 0
    for cb in codebases:
        name = cb["name"]
        cid = cb["id"]
        count = db.rebuild_fts_index(cid)
        print(f"  {name}: {count} snippets indexed into FTS5")
        total += count

    print(f"\nDone. {total} total FTS5 rows populated.")


if __name__ == "__main__":
    main()
