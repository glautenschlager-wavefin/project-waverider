#!/usr/bin/env python3
"""
List all built indices.
"""

import sys
import os
from pathlib import Path
import json

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main():
    """List all indices."""
    indices_dir = Path("indices")

    if not indices_dir.exists():
        print("No indices directory found.")
        return 1

    metadata_files = list(indices_dir.glob("*_metadata.json"))

    if not metadata_files:
        print("No indices found.")
        return 0

    print("=" * 70)
    print("Available Indices")
    print("=" * 70)

    for metadata_file in sorted(metadata_files):
        with open(metadata_file) as f:
            metadata = json.load(f)

        print(f"\nIndex: {metadata['index_name']}")
        print(f"  Codebase: {metadata['codebase_path']}")
        print(f"  Embeddings: {metadata['embedding_provider']} ({metadata['embedding_model']})")
        print(f"  Files indexed: {metadata.get('total_files_indexed', 'N/A')}")
        print(f"  Snippets: {metadata.get('total_snippets', 0)}")
        print(f"  Embeddings: {metadata.get('total_embeddings', 0)}")
        print(f"  Indexed at: {metadata.get('indexed_at', 'N/A')}")

    print("\n" + "=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
