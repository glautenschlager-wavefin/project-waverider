#!/usr/bin/env python3
"""
Initialize Neo4j schema for Waverider.
"""

import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.neo4j_graph import Neo4jGraphManager


def main():
    """Initialize Neo4j schema."""
    print("Initializing Neo4j schema...")

    try:
        neo4j = Neo4jGraphManager()

        neo4j.init_schema()
        print("✓ Neo4j schema initialized successfully")

        # Get connection info
        print(f"✓ Connected to: {neo4j.uri}")
        print("✓ You can access the Neo4j browser at: http://localhost:7474/browser/")

        neo4j.close()
        return 0

    except ConnectionError as e:
        print(f"✗ Connection error: {e}")
        print(
            "\nMake sure Neo4j is running. You can start it with:\n"
            "  docker run --name waverider-neo4j -p 7474:7474 -p 7687:7687 "
            "-e NEO4J_AUTH=neo4j/password neo4j:latest"
        )
        return 1
    except PermissionError as e:
        print(f"✗ Authentication error: {e}")
        print("\nSet NEO4J_USER and NEO4J_PASSWORD in your shell or project .env file.")
        return 1
    except ValueError as e:
        print(f"✗ Configuration error: {e}")
        print("\nSet NEO4J_USER and NEO4J_PASSWORD in your shell or project .env file.")
        return 1
    except Exception as e:
        print(f"✗ Error initializing Neo4j: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
