#!/usr/bin/env python3
"""End-to-end validation of Waverider search pipeline (Phase 3.1).

Tests semantic search, keyword search, and hybrid ranking on the indexed Waverider repo.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager
from waverider.embeddings import get_embedding_provider
from waverider.fusion import rrf_fuse


def validate_search():
    """Run end-to-end search validation."""
    print("=" * 80)
    print("Waverider Search Validation — Phase 3.1")
    print("=" * 80)

    # Initialize database and embeddings
    print("\n1. Initializing database and embeddings...")
    try:
        db = DatabaseManager()
        embeddings = get_embedding_provider(provider="ollama", model="nomic-embed-text")
        codebase_id = db.get_codebase("waverider")["id"]
        print(f"   ✓ Database initialized (codebase_id={codebase_id})")
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        return False

    # Test queries
    test_queries = [
        ("database connection pooling", "Should find database.py with pool setup"),
        ("vector embedding similarity search", "Should find embeddings.py and search methods"),
        ("tree sitter code extraction", "Should find treesitter_parser.py"),
        ("mcp server implementation", "Should find mcp_server.py with tool definitions"),
        ("async context management", "Should find async/await patterns in indexer"),
    ]

    all_passed = True

    for query, expected in test_queries:
        print(f"\n2. Testing query: '{query}'")
        print(f"   Expected: {expected}")

        try:
            # Semantic search
            query_vec = embeddings.embed(query)
            vector_results = db.search_embeddings(
                query_embedding=query_vec,
                codebase_id=codebase_id,
                limit=3,
            )

            # Keyword search
            keyword_results = db.search_bm25(query=query, codebase_id=codebase_id, limit=3)

            # Hybrid ranking
            fused = rrf_fuse(
                {"vector": vector_results, "keyword": keyword_results},
                id_key="id",
                limit=3,
            )

            if not fused and not vector_results:
                print(f"   ✗ No results found")
                all_passed = False
                continue

            results = fused if fused else vector_results
            print(f"   ✓ Found {len(results)} results:")

            for i, r in enumerate(results, 1):
                sim = r.get("rrf_score") or r.get("similarity", "?")
                print(f"      [{i}] {r['file_path']} ({r['snippet_type']}: {r['name']}) score={sim:.3f}")
                print(f"          {r['content'][:80].replace(chr(10), ' ')}...")

        except Exception as e:
            print(f"   ✗ Error: {e}")
            import traceback
            traceback.print_exc()
            all_passed = False

    db.close()

    print("\n" + "=" * 80)
    if all_passed:
        print("✓ All validation tests completed successfully")
    else:
        print("⚠ Some tests failed or returned no results")
    print("=" * 80)

    return all_passed


if __name__ == "__main__":
    success = validate_search()
    sys.exit(0 if success else 1)
