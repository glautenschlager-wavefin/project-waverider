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

    except PermissionError as e:
        print(f"✗ Authentication error: {e}")
        print("\nChecks:")
        print("  1. Confirm Neo4j is running: make neo4j-status")
        print("  2. Confirm the Waverider client sees your password: echo $NEO4J_PASSWORD")
        print("  3. Or create a project .env file with NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD")
        print("  4. Verify the password matches the one configured inside Neo4j itself")
        return 1

    except ValueError as e:
        print(f"✗ Configuration error: {e}")
        print("\nThe client cannot see your Neo4j credentials.")
        print("  1. Export them in this shell before running Poetry commands")
        print("  2. Or create a project .env file from .env.example")
        print("  3. Then rerun: poetry run python scripts/test_neo4j_connection.py")
        return 1

    except Exception as e:
        print(f"✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
