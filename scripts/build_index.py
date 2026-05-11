#!/usr/bin/env python3
"""Build vector indices over a codebase using CocoIndex (Phase 2).

CocoIndex handles all incremental processing: it detects which files changed
since the last run and only re-extracts snippets + re-embeds those files.
The first run is a full index; subsequent runs are incremental.

Examples:
  # Index a codebase (incremental by default)
  python build_index.py --codebase-path /path/to/code --index-name myproject

  # Force a full reindex (re-embed everything)
  python build_index.py --codebase-path /path/to/code --index-name myproject --full

  # Also build the Neo4j knowledge graph
  python build_index.py --codebase-path /path/to/code --index-name myproject --use-neo4j
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Add src to Python path so waverider imports resolve when run as a script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cocoindex as coco

from waverider.cocoindex_app import make_app
from waverider.database import DatabaseManager
from waverider.neo4j_graph import Neo4jGraphManager


def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build vector indices over a codebase (CocoIndex incremental)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--codebase-path", required=True, help="Path to the codebase root")
    parser.add_argument("--index-name", required=True, help="Unique name for this index")
    parser.add_argument("--description", default="", help="Human-readable codebase description")
    parser.add_argument("--language", default="mixed", help="Primary language (default: mixed)")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force full reindex — re-embed every file even if unchanged",
    )
    parser.add_argument(
        "--use-neo4j",
        action="store_true",
        help="Also populate the Neo4j knowledge graph after indexing",
    )
    return parser.parse_args()


async def _run_cocoindex(app: coco.App, *, full: bool) -> None:
    """Run one CocoIndex update pass inside a runtime context."""
    async with coco.runtime():
        if full:
            await coco.show_progress(app.update(full_reprocess=True))
        else:
            await coco.show_progress(app.update())


def main() -> int:
    args = _parse_args()

    codebase_path = Path(args.codebase_path).resolve()
    if not codebase_path.exists():
        print(f"✗ Codebase path does not exist: {codebase_path}")
        return 1

    # ------------------------------------------------------------------
    # 1. Register codebase in the codebase_metadata table.
    #    This is a lightweight UPSERT so `list_codebases()` works in the
    #    MCP server even before CocoIndex finishes the first full pass.
    # ------------------------------------------------------------------
    print("Registering codebase in metadata table...")
    try:
        db = DatabaseManager()
        db.init_schema()
        db.add_codebase(
            name=args.index_name,
            path=str(codebase_path),
            description=args.description,
            language=args.language,
        )
        db.close()
        print(f"✓ Registered '{args.index_name}' in codebase_metadata")
    except Exception as exc:
        print(f"✗ Could not register codebase metadata: {exc}")
        return 1

    # ------------------------------------------------------------------
    # 2. Run CocoIndex to index files, extract snippets, and embed them.
    #    CocoIndex stores its memoisation state in the database pointed to
    #    by COCOINDEX_DB_URL (or falls back to DATABASE_URL).
    # ------------------------------------------------------------------
    print(f"\nStarting {'full' if args.full else 'incremental'} CocoIndex pass...")
    print(f"  Codebase : {codebase_path}")
    print(f"  Index    : {args.index_name}")
    print(f"  Backend  : {os.getenv('DATABASE_URL', '(default localhost:5432/waverider)')}")

    try:
        app = make_app(codebase_name=args.index_name, sourcedir=codebase_path)
        asyncio.run(_run_cocoindex(app, full=args.full))
        print("\n✓ CocoIndex pass complete")
    except Exception as exc:
        print(f"\n✗ CocoIndex indexing failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    # ------------------------------------------------------------------
    # 3. (Optional) Build Neo4j knowledge graph.
    # ------------------------------------------------------------------
    if args.use_neo4j:
        print("\nBuilding Neo4j knowledge graph...")
        try:
            neo4j = Neo4jGraphManager()
            neo4j.init_schema()
            neo4j.add_codebase(
                name=args.index_name,
                path=str(codebase_path),
                description=args.description,
            )
            neo4j.close()
            print("✓ Neo4j graph populated")
        except Exception as exc:
            print(f"⚠ Neo4j population failed (non-fatal): {exc}")

    # ------------------------------------------------------------------
    # 4. Save lightweight metadata JSON (for tooling / observability).
    # ------------------------------------------------------------------
    metadata = {
        "index_name": args.index_name,
        "codebase_path": str(codebase_path),
        "indexer": "cocoindex",
        "embedding_model": os.getenv("OLLAMA_MODEL", "nomic-embed-text"),
        "full_reindex": args.full,
        "neo4j": args.use_neo4j,
    }
    metadata_path = Path("indices") / f"{args.index_name}_metadata.json"
    metadata_path.parent.mkdir(exist_ok=True)
    with open(metadata_path, "w") as fh:
        json.dump(metadata, fh, indent=2)
    print(f"✓ Metadata saved to: {metadata_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
