#!/usr/bin/env python3
"""
Initialize SQLite database for Waverider.
"""

import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager


def main():
    """Initialize database."""
    print("Initializing SQLite database...")

    db = DatabaseManager(db_path="data/waverider.db")

    try:
        db.init_schema()
        print("✓ Database initialized successfully")
        print(f"✓ Database file: {db.db_path}")
        return 0
    except Exception as e:
        print(f"✗ Error initializing database: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
