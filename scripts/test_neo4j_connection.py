#!/usr/bin/env python3
"""
Test Neo4j connection.
"""

import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.neo4j_graph import Neo4jGraphManager


def main():
    """Test Neo4j connection."""
    print("Testing Neo4j connection...")

    try:
        neo4j = Neo4jGraphManager()
        print("✓ Connected to Neo4j successfully")

        # Get connection info
        print(f"✓ URI: {neo4j.uri}")

        # Try a simple query
        result = neo4j.query("RETURN 1 as test")
        print(f"✓ Query successful: {result}")

        # Check what's in the database
        result = neo4j.query("MATCH (n) RETURN COUNT(n) as node_count")
        node_count = result[0]["node_count"] if result else 0
        print(f"✓ Nodes in database: {node_count}")

        neo4j.close()
        print("\n✓ Neo4j is working correctly!")
        print("\nAccess Neo4j Browser at: http://localhost:7474/browser/")

        return 0

    except ConnectionError as e:
        print(f"✗ Connection error: {e}")
        print("\nMake sure Neo4j is running:")
        print("  Option 1 - Docker:")
        print(
            "    docker run --name waverider-neo4j -p 7474:7474 -p 7687:7687 "
            "-e NEO4J_AUTH=neo4j/password neo4j:latest"
        )
        print("\n  Option 2 - Homebrew (macOS):")
        print("    brew install neo4j")
        print("    brew services start neo4j")
        return 1

    except Exception as e:
        print(f"✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
