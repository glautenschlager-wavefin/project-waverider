#!/usr/bin/env python3
"""
Build vector indices over a codebase.
"""

import sys
import os
import argparse
import json
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager
from waverider.indexer import CodebaseIndexer
from waverider.embeddings import get_embedding_provider
from waverider.neo4j_graph import Neo4jGraphManager


def main():
    """Build indices for a codebase."""
    parser = argparse.ArgumentParser(
        description="Build vector indices over a codebase",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Index with both SQLite and Neo4j
  python build_index.py --codebase-path /path/to/code --index-name myproject

  # Index with SQLite only (faster)
  python build_index.py --codebase-path /path/to/code --index-name myproject --use-sqlite

  # Use OpenAI embeddings (requires OPENAI_API_KEY)
  OPENAI_API_KEY=sk-... python build_index.py --codebase-path /path/to/code

  # Use mock embeddings for testing (no API key needed)
  python build_index.py --codebase-path /path/to/code --embedding-provider mock
        """,
    )

    parser.add_argument(
        "--codebase-path",
        required=True,
        help="Path to the codebase to index",
    )
    parser.add_argument(
        "--index-name",
        required=True,
        help="Unique name for this index",
    )
    parser.add_argument(
        "--description",
        default="",
        help="Description of the codebase",
    )
    parser.add_argument(
        "--use-sqlite",
        action="store_true",
        default=True,
        help="Build SQLite indices (default: true)",
    )
    parser.add_argument(
        "--use-neo4j",
        action="store_true",
        help="Build Neo4j knowledge graph",
    )
    parser.add_argument(
        "--embedding-provider",
        choices=["openai", "mock"],
        default="openai",
        help="Embedding provider (default: openai)",
    )
    parser.add_argument(
        "--model",
        default="text-embedding-3-small",
        help="Embedding model (default: text-embedding-3-small)",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        help="Patterns to exclude from indexing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Batch size for embedding generation (default: 10)",
    )
    parser.add_argument(
        "--db-path",
        default="data/waverider.db",
        help="Path to SQLite database (default: data/waverider.db)",
    )

    args = parser.parse_args()

    # Validate paths
    codebase_path = Path(args.codebase_path)
    if not codebase_path.exists():
        print(f"✗ Codebase path does not exist: {codebase_path}")
        return 1

    # Initialize database
    print("Setting up database...")
    db = DatabaseManager(db_path=args.db_path)
    db.init_schema()

    # Get embedding provider
    print(f"Using embedding provider: {args.embedding_provider}")
    try:
        embeddings = get_embedding_provider(
            provider=args.embedding_provider, model=args.model
        )
    except Exception as e:
        print(f"✗ Error initializing embedding provider: {e}")
        return 1

    # Create indexer
    indexer = CodebaseIndexer(
        db_manager=db,
        embedding_provider=embeddings,
        exclude_patterns=args.exclude,
    )

    # Index codebase
    try:
        print("\n" + "=" * 60)
        print("Starting codebase indexing...")
        print("=" * 60 + "\n")

        stats = indexer.index_codebase(
            codebase_name=args.index_name,
            codebase_path=str(codebase_path),
            description=args.description,
            batch_size=args.batch_size,
        )

        print("\n" + "=" * 60)
        print("Indexing complete!")
        print("=" * 60)
        print(f"Files indexed: {stats['total_files_indexed']}")
        print(f"Snippets extracted: {stats['total_snippets']}")
        print(f"Embeddings generated: {stats['total_embeddings']}")

        # Optional: Build Neo4j graph
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
                print("✓ Neo4j graph populated (basic schema)")
                neo4j.close()
            except Exception as e:
                print(f"⚠ Could not initialize Neo4j: {e}")

        # Save index metadata
        metadata = {
            "index_name": args.index_name,
            "codebase_path": str(codebase_path),
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.model,
            **stats,
        }

        metadata_path = Path("indices") / f"{args.index_name}_metadata.json"
        metadata_path.parent.mkdir(exist_ok=True)
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        print(f"\n✓ Index metadata saved to: {metadata_path}")
        print("\nNext steps:")
        print(f"  - Query the index: python scripts/index_stats.py --index-name {args.index_name}")
        print(f"  - List all indices: python scripts/list_indices.py")

        return 0

    except Exception as e:
        print(f"\n✗ Error during indexing: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
