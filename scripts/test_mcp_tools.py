#!/usr/bin/env python3
"""Test Phase 4.1: MCP Tool Integration Testing.

Directly call the MCP tools (retrieve_code, search_codebase, neo4j_status)
to validate they work end-to-end with the indexed Waverider codebase.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Set up environment
os.environ.setdefault("DATABASE_URL", "postgresql://waverider:changeme@localhost:5432/waverider")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "nomic-embed-text")


def test_retrieve_code():
    """Test semantic code retrieval via MCP tool."""
    print("\n" + "=" * 80)
    print("TEST 1: retrieve_code (Semantic Retrieval)")
    print("=" * 80)

    # Import the tool directly from mcp_server
    from waverider.mcp_server import retrieve_code

    test_queries = [
        "database connection pooling",
        "embedding vectors",
        "parse code snippets",
    ]

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        print("-" * 80)
        result = retrieve_code(query=query, codebase_name="waverider", limit=3)
        print(result)
        print()


def test_search_codebase():
    """Test graph-based keyword search via MCP tool."""
    print("\n" + "=" * 80)
    print("TEST 2: search_codebase (Graph-based Keyword Search)")
    print("=" * 80)

    from waverider.mcp_server import search_codebase

    test_queries = [
        "DatabaseManager",
        "IndexerError",
        "embeddings",
    ]

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        print("-" * 80)
        result = search_codebase(query=query, codebase_name="waverider", limit=5)
        print(result)
        print()


def test_neo4j_status():
    """Test Neo4j status and connection."""
    print("\n" + "=" * 80)
    print("TEST 3: neo4j_status (Graph Connection Check)")
    print("=" * 80)

    from waverider.mcp_server import neo4j_status

    print()
    result = neo4j_status()
    print(result)
    print()


def main():
    """Run all MCP integration tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "Phase 4.1: MCP Tool Integration Testing".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")

    success = True

    try:
        test_retrieve_code()
    except Exception as e:
        print(f"\n✗ retrieve_code test failed: {e}")
        import traceback
        traceback.print_exc()
        success = False

    try:
        test_search_codebase()
    except Exception as e:
        print(f"\n✗ search_codebase test failed: {e}")
        import traceback
        traceback.print_exc()
        success = False

    try:
        test_neo4j_status()
    except Exception as e:
        print(f"\n✗ neo4j_status test failed: {e}")
        import traceback
        traceback.print_exc()
        success = False

    print("\n" + "=" * 80)
    if success:
        print("✓ All MCP integration tests completed")
    else:
        print("⚠ Some tests failed")
    print("=" * 80)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
