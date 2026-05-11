#!/usr/bin/env python3
"""Build vector indices over a codebase — Phase 3 (Neo4j Integration).

Uses the manual indexer (Phase 1.1 approach) for now.
CocoIndex integration will be completed after resolving schema mapping issues.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager
from waverider.indexer import CodebaseIndexer
from waverider.neo4j_graph import Neo4jGraphManager


def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build vector indices over a codebase with optional Neo4j knowledge graph"
    )
    parser.add_argument("--codebase-path", required=True, help="Path to the codebase to index")
    parser.add_argument("--index-name", required=True, help="Unique name for this index")
    parser.add_argument("--description", default="", help="Description of the codebase")
    parser.add_argument("--language", default="python", help="Primary language")
    parser.add_argument("--full", action="store_true", help="Force full reindex")
    parser.add_argument("--use-neo4j", action="store_true", help="Build Neo4j knowledge graph")
    parser.add_argument(
        "--embedding-provider", choices=["ollama", "mock"], default="ollama", help="Embedding provider"
    )
    parser.add_argument("--model", default="nomic-embed-text", help="Embedding model")

    return parser.parse_args()


def main() -> int:
    """Build indices and optionally populate Neo4j (Phase 3)."""
    args = _parse_args()

    codebase_path = Path(args.codebase_path).resolve()
    if not codebase_path.exists():
        print(f"✗ Codebase path does not exist: {codebase_path}")
        return 1

    print("=" * 70)
    print("Waverider Index Builder — Phase 3 (Manual Indexer + Neo4j)")
    print("=" * 70)

    # 1. Register codebase
    print("\n1. Registering codebase in metadata...")
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
        from waverider.embeddings import get_embedding_provider

        embeddings = get_embedding_provider(provider=args.embedding_provider, model=args.model)
        indexer = CodebaseIndexer(db_manager=db, embedding_provider=embeddings, exclude_patterns=[])

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

    # 3. Build Neo4j graph (Phase 3 — NEW)
    if args.use_neo4j:
        print("\n3. Building Neo4j knowledge graph...")
        try:
            neo4j = Neo4jGraphManager()
            neo4j.init_schema()
            stats = neo4j.populate_from_coco(codebase_name=args.index_name, db=db)
            neo4j.close()
            print(f"   ✓ Created {stats['files']} file nodes")
            print(f"   ✓ Created {stats['functions']} function nodes")
            print(f"   ✓ Created {stats['classes']} class nodes")
            print(f"   ✓ Extracted {stats['imports']} imports")
        except Exception as e:
            print(f"   ⚠ Neo4j failed (non-fatal): {e}")

    # 4. Save metadata
    print("\n4. Saving metadata...")
    metadata = {
        "index_name": args.index_name,
        "codebase_path": str(codebase_path),
        "indexer": "manual",
        "embedding_model": args.model,
        "full_reindex": args.full,
        "neo4j": args.use_neo4j,
    }
    metadata_path = Path("indices") / f"{args.index_name}_metadata.json"
    metadata_path.parent.mkdir(exist_ok=True)
    with open(metadata_path, "w") as fh:
        json.dump(metadata, fh, indent=2)
    print(f"   ✓ Saved to {metadata_path}")

    db.close()
    print("\n" + "=" * 70)
    print("✓ Phase 3 indexing complete")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
