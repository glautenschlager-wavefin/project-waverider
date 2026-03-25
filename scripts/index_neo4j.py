#!/usr/bin/env python3
"""
Index a codebase into Neo4j: creates Codebase, CodeFile, Function, and Class nodes.

Usage:
    poetry run python scripts/index_neo4j.py --codebase-path ./src --index-name waverider
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.indexer import CodebaseIndexer
from waverider.neo4j_graph import Neo4jGraphManager
from waverider.database import DatabaseManager
from waverider.embeddings import get_embedding_provider
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Index a codebase into Neo4j")
    parser.add_argument("--codebase-path", required=True, help="Root path to index")
    parser.add_argument("--index-name", required=True, help="Codebase identifier in Neo4j")
    parser.add_argument("--description", default="", help="Optional description")
    parser.add_argument("--clear", action="store_true", help="Delete existing nodes for this codebase first")
    args = parser.parse_args()

    codebase_path = Path(args.codebase_path).resolve()
    if not codebase_path.exists():
        print(f"✗ Path does not exist: {codebase_path}")
        return 1

    print(f"Connecting to Neo4j...")
    try:
        neo4j = Neo4jGraphManager()
    except Exception as e:
        print(f"✗ Neo4j connection failed: {e}")
        return 1

    if args.clear:
        print("Clearing existing nodes for this codebase...")
        neo4j.query(
            """
            MATCH (cb:Codebase {name: $name})
            OPTIONAL MATCH (cb)-[:CONTAINS_FILE]->(f:CodeFile)
            OPTIONAL MATCH (f)-[:CONTAINS_FUNCTION]->(fn:Function)
            OPTIONAL MATCH (f)-[:CONTAINS_CLASS]->(cl:Class)
            DETACH DELETE fn, cl, f, cb
            """,
            name=args.index_name,
        )
        print("  Done.")

    print(f"Creating Codebase node: {args.index_name}")
    neo4j.add_codebase(
        name=args.index_name,
        path=str(codebase_path),
        description=args.description,
    )

    # Reuse CodebaseIndexer's file discovery and AST parsing
    dummy_db = DatabaseManager(db_path=":memory:")
    dummy_embeddings = get_embedding_provider(provider="mock")
    indexer = CodebaseIndexer(db_manager=dummy_db, embedding_provider=dummy_embeddings)

    files = indexer.get_files_to_index(str(codebase_path))
    print(f"Found {len(files)} files to index\n")

    total_files = total_functions = total_classes = 0

    for file_path in files:
        relative_path = str(file_path.relative_to(codebase_path))
        language = indexer.SUPPORTED_EXTENSIONS.get(file_path.suffix, "unknown")

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except Exception as e:
            print(f"  SKIP {relative_path}: {e}")
            continue

        import hashlib
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        neo4j.add_code_file(
            codebase_name=args.index_name,
            file_path=relative_path,
            file_type=language,
            content_hash=content_hash,
        )
        total_files += 1

        snippets = indexer.extract_snippets(file_path, content)
        file_funcs = file_classes = 0

        for snippet in snippets:
            if snippet.snippet_type == "function":
                neo4j.add_function(
                    file_path=relative_path,
                    function_name=snippet.name,
                    language=language,
                    signature=snippet.content.splitlines()[0][:200],
                )
                file_funcs += 1
                total_functions += 1
            elif snippet.snippet_type == "class":
                neo4j.add_class(
                    file_path=relative_path,
                    class_name=snippet.name,
                    language=language,
                )
                file_classes += 1
                total_classes += 1

        print(f"  {relative_path}: {file_funcs} functions, {file_classes} classes")

    neo4j.close()

    print(f"\n{'='*50}")
    print(f"Indexing complete!")
    print(f"  Files:     {total_files}")
    print(f"  Functions: {total_functions}")
    print(f"  Classes:   {total_classes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
