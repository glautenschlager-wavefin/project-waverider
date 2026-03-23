#!/usr/bin/env python3
"""
Get statistics for a specific index.
"""

import sys
import os
import argparse

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager


def main():
    """Get index statistics."""
    parser = argparse.ArgumentParser(description="Get statistics for an index")
    parser.add_argument(
        "--index-name",
        required=True,
        help="Name of the index",
    )
    parser.add_argument(
        "--db-path",
        default="data/waverider.db",
        help="Path to SQLite database",
    )

    args = parser.parse_args()

    db = DatabaseManager(db_path=args.db_path)

    # Find codebase by name
    codebase = db.get_codebase(args.index_name)

    if not codebase:
        print(f"✗ Index not found: {args.index_name}")
        print("\nAvailable indices:")
        for cb in db.list_codebases():
            print(f"  - {cb['name']}")
        return 1

    # Get statistics
    stats = db.get_statistics(codebase["id"])

    print("=" * 60)
    print(f"Index: {codebase['name']}")
    print("=" * 60)
    print(f"Codebase path: {codebase['path']}")
    print(f"Description: {codebase['description']}")
    print(f"Language: {codebase['language']}")
    print(f"Created: {codebase['created_at']}")
    print(f"Updated: {codebase['updated_at']}")
    print()
    print("Statistics:")
    print(f"  Files: {stats['total_files']}")
    print(f"  Snippets: {stats['total_snippets']}")
    print(f"  Embeddings: {stats['total_embeddings']}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
