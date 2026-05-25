#!/usr/bin/env python3
"""Build vector indices over a codebase — Phase 4 (CocoIndex + Postgres).

Default mode: CocoIndex incremental indexer targeting Postgres/pgVector.
Legacy mode (--legacy): manual indexer for debugging or rollback.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build vector indices over a codebase (CocoIndex incremental by default)"
    )
    parser.add_argument("--codebase-path", required=True, help="Path to the codebase to index")
    parser.add_argument("--index-name", required=True, help="Unique name for this index")
    parser.add_argument("--description", default="", help="Description of the codebase")
    parser.add_argument("--language", default="python", help="Primary language")
    parser.add_argument(
        "--legacy", action="store_true",
        help="Use legacy manual indexer instead of CocoIndex",
    )
    parser.add_argument("--full", action="store_true", help="Force full reindex (legacy mode only)")
    parser.add_argument("--use-neo4j", action="store_true", help="Build Neo4j knowledge graph")
    parser.add_argument(
        "--embedding-provider", choices=["ollama", "mock"], default="ollama",
        help="Embedding provider (legacy mode only)",
    )
    parser.add_argument("--model", default="nomic-embed-text", help="Embedding model")
    parser.add_argument(
        "--exclude", nargs="*", default=[], help="Additional directory patterns to exclude",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# CocoIndex path (default)
# ---------------------------------------------------------------------------


def _run_cocoindex(args) -> int:
    """Run incremental indexing via CocoIndex against Postgres."""
    import cocoindex as coco
    from waverider.cocoindex_app import make_app

    codebase_path = Path(args.codebase_path).resolve()

    print("=" * 70)
    print("Waverider Index Builder — CocoIndex Incremental")
    print("=" * 70)
    print(f"  Codebase: {args.index_name}")
    print(f"  Path:     {codebase_path}")
    print(f"  Model:    {args.model}")
    print()

    app = make_app(args.index_name, codebase_path)

    async def _update():
        async with coco.runtime():
            await app.update()

    asyncio.run(_update())

    # Register codebase metadata in the main DB so search can find it
    from waverider.database import DatabaseManager

    db = DatabaseManager()
    db.init_schema()
    db.add_codebase(
        name=args.index_name,
        path=str(codebase_path),
        description=args.description,
        language=args.language,
    )
    db.close()

    # Optionally build Neo4j graph
    if args.use_neo4j:
        _build_neo4j(args)

    # Save metadata
    _save_metadata(args, codebase_path, indexer="cocoindex")

    print("\n" + "=" * 70)
    print("✓ CocoIndex incremental update complete")
    print("=" * 70)
    return 0


# ---------------------------------------------------------------------------
# Legacy manual indexer path (fallback)
# ---------------------------------------------------------------------------


def _run_legacy(args) -> int:
    """Run indexing via the legacy manual indexer."""
    from waverider.database import DatabaseManager
    from waverider.embeddings import get_embedding_provider
    from waverider.indexer import CodebaseIndexer

    codebase_path = Path(args.codebase_path).resolve()

    print("=" * 70)
    print("Waverider Index Builder — Legacy Manual Indexer")
    print("=" * 70)
    print(f"  Codebase: {args.index_name}")
    print(f"  Path:     {codebase_path}")
    print(f"  Mode:     {'full rebuild' if args.full else 'incremental'}")
    print()

    # 1. Register codebase
    print("1. Registering codebase in metadata...")
    try:
        db = DatabaseManager()
        db.init_schema()
        db.add_codebase(
            name=args.index_name,
            path=str(codebase_path),
            description=args.description,
            language=args.language,
        )
        print(f"   ✓ Registered '{args.index_name}'")
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        return 1

    # 2. Index codebase
    print("\n2. Indexing codebase...")
    try:
        embeddings = get_embedding_provider(provider=args.embedding_provider, model=args.model)
        indexer = CodebaseIndexer(
            db_manager=db, embedding_provider=embeddings, exclude_patterns=args.exclude,
        )

        stats = indexer.index_codebase(
            codebase_name=args.index_name,
            codebase_path=str(codebase_path),
            description=args.description,
            batch_size=10,
            incremental=not args.full,
        )

        print(f"   ✓ Indexed {stats['total_files_indexed']} files")
        print(f"   ✓ Created {stats['total_snippets']} snippets")
        print(f"   ✓ Generated {stats['total_embeddings']} embeddings")
    except Exception as e:
        print(f"   ✗ Indexing failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    # 3. Build Neo4j graph
    if args.use_neo4j:
        _build_neo4j(args)

    # 4. Save metadata
    _save_metadata(args, codebase_path, indexer="manual")

    db.close()
    print("\n" + "=" * 70)
    print("✓ Legacy indexing complete")
    print("=" * 70)
    return 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_neo4j(args) -> None:
    """Build Neo4j knowledge graph (non-fatal on failure)."""
    from waverider.database import DatabaseManager
    from waverider.neo4j_graph import Neo4jGraphManager

    print("\n  Building Neo4j knowledge graph...")
    try:
        db = DatabaseManager()
        neo4j = Neo4jGraphManager()
        neo4j.init_schema()
        stats = neo4j.populate_from_coco(codebase_name=args.index_name, db=db)
        neo4j.close()
        db.close()
        print(f"   ✓ Created {stats['files']} file nodes")
        print(f"   ✓ Created {stats['functions']} function nodes")
        print(f"   ✓ Created {stats['classes']} class nodes")
        print(f"   ✓ Extracted {stats['imports']} imports")
    except Exception as e:
        print(f"   ⚠ Neo4j failed (non-fatal): {e}")


def _save_metadata(args, codebase_path: Path, indexer: str) -> None:
    """Write index metadata JSON."""
    print("\n  Saving metadata...")
    metadata = {
        "index_name": args.index_name,
        "codebase_path": str(codebase_path),
        "indexer": indexer,
        "embedding_model": args.model,
        "full_reindex": args.full if hasattr(args, "full") else False,
        "neo4j": args.use_neo4j,
    }
    metadata_path = Path("indices") / f"{args.index_name}_metadata.json"
    metadata_path.parent.mkdir(exist_ok=True)
    with open(metadata_path, "w") as fh:
        json.dump(metadata, fh, indent=2)
    print(f"   ✓ Saved to {metadata_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    args = _parse_args()

    codebase_path = Path(args.codebase_path).resolve()
    if not codebase_path.exists():
        print(f"✗ Codebase path does not exist: {codebase_path}")
        return 1

    if args.legacy:
        return _run_legacy(args)
    return _run_cocoindex(args)


if __name__ == "__main__":
    sys.exit(main())
