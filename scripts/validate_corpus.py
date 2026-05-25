#!/usr/bin/env python3
"""Phase 5, Step 16: Side-by-side corpus validation.

Compares the CocoIndex (coco_snippets) index against the legacy schema
(code_snippets + embeddings) for large repos to validate:
  1. Count parity — snippet and embedding totals
  2. Retrieval quality — top-k overlap for representative queries
  3. Incremental latency — time to re-index after a small file delta

Usage:
    python scripts/validate_corpus.py --codebase-name waverider
    python scripts/validate_corpus.py --codebase-name identity --queries "login flow" "user model"
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx

from waverider.database import DatabaseManager
from waverider.fusion import rrf_fuse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed_query(query: str) -> list[float]:
    """Generate an embedding vector for a query string via Ollama."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "nomic-embed-text")
    resp = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": model, "prompt": query},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def _coco_count(db: DatabaseManager, codebase_name: str) -> int:
    """Count rows in coco_snippets for this codebase."""
    with db._conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM coco_snippets WHERE codebase_name = %s",
            (codebase_name,),
        ).fetchone()
    return row["c"] if row else 0


def _legacy_count(db: DatabaseManager, codebase_id: int) -> dict:
    """Count rows in old schema tables for this codebase."""
    return db.get_statistics(codebase_id)


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def validate_count_parity(db: DatabaseManager, codebase_name: str, codebase_id: int) -> dict:
    """Compare snippet counts between CocoIndex and legacy schemas."""
    print("\n" + "=" * 60)
    print("1. COUNT PARITY")
    print("=" * 60)

    coco_rows = _coco_count(db, codebase_name)
    legacy_stats = _legacy_count(db, codebase_id)
    legacy_snippets = legacy_stats.get("total_snippets", 0)
    legacy_embeddings = legacy_stats.get("total_embeddings", 0)

    print(f"  CocoIndex (coco_snippets):  {coco_rows} rows")
    print(f"  Legacy (code_snippets):     {legacy_snippets} rows")
    print(f"  Legacy (embeddings):        {legacy_embeddings} rows")

    if legacy_snippets > 0:
        ratio = coco_rows / legacy_snippets
        print(f"  Coco/Legacy ratio:          {ratio:.2f}")
        parity_ok = 0.8 <= ratio <= 1.5
    else:
        ratio = None
        parity_ok = coco_rows > 0
        print("  (No legacy data — parity check skipped)")

    status = "PASS" if parity_ok else "WARN"
    print(f"  Status: {status}")

    return {
        "coco_rows": coco_rows,
        "legacy_snippets": legacy_snippets,
        "legacy_embeddings": legacy_embeddings,
        "ratio": ratio,
        "pass": parity_ok,
    }


def validate_retrieval_quality(
    db: DatabaseManager,
    codebase_name: str,
    codebase_id: int,
    queries: list[str],
    limit: int = 10,
) -> dict:
    """Compare top-k retrieval results between CocoIndex and legacy for given queries."""
    print("\n" + "=" * 60)
    print("2. RETRIEVAL QUALITY")
    print("=" * 60)

    results = []

    for query in queries:
        print(f"\n  Query: '{query}'")
        try:
            vec = _embed_query(query)
        except Exception as e:
            print(f"    ✗ Embedding failed: {e}")
            results.append({"query": query, "error": str(e)})
            continue

        # CocoIndex path
        coco_vector = db.search_coco_embeddings(vec, codebase_name, limit=limit)
        coco_bm25 = db.search_coco_bm25(query, codebase_name, limit=limit)

        # Legacy path
        legacy_vector = db.search_embeddings(vec, codebase_id, limit=limit)
        legacy_bm25 = db.search_bm25(query, codebase_id, limit=limit)

        # Compare by name overlap
        coco_names = {r["name"] for r in coco_vector}
        legacy_names = {r["name"] for r in legacy_vector}
        overlap = coco_names & legacy_names
        jaccard = len(overlap) / len(coco_names | legacy_names) if (coco_names | legacy_names) else 0

        print(f"    Vector: coco={len(coco_vector)}, legacy={len(legacy_vector)}, "
              f"overlap={len(overlap)}/{len(coco_names | legacy_names)} (J={jaccard:.2f})")
        print(f"    BM25:   coco={len(coco_bm25)}, legacy={len(legacy_bm25)}")

        # Hybrid fusion comparison
        if coco_vector and coco_bm25:
            coco_fused = rrf_fuse(
                {"vector": coco_vector, "keyword": coco_bm25}, id_key="id", limit=limit,
            )
        else:
            coco_fused = coco_vector[:limit]

        if legacy_vector and legacy_bm25:
            legacy_fused = rrf_fuse(
                {"vector": legacy_vector, "keyword": legacy_bm25}, id_key="id", limit=limit,
            )
        else:
            legacy_fused = legacy_vector[:limit]

        fused_coco_names = {r["name"] for r in coco_fused}
        fused_legacy_names = {r["name"] for r in legacy_fused}
        fused_overlap = fused_coco_names & fused_legacy_names
        fused_jaccard = (
            len(fused_overlap) / len(fused_coco_names | fused_legacy_names)
            if (fused_coco_names | fused_legacy_names)
            else 0
        )
        print(f"    Hybrid: coco={len(coco_fused)}, legacy={len(legacy_fused)}, "
              f"overlap={len(fused_overlap)} (J={fused_jaccard:.2f})")

        results.append({
            "query": query,
            "vector_jaccard": jaccard,
            "hybrid_jaccard": fused_jaccard,
            "coco_count": len(coco_vector),
            "legacy_count": len(legacy_vector),
        })

    # Summary
    avg_jaccard = sum(r.get("vector_jaccard", 0) for r in results) / max(len(results), 1)
    print(f"\n  Average vector Jaccard similarity: {avg_jaccard:.2f}")
    quality_ok = avg_jaccard >= 0.3 or all(r.get("coco_count", 0) > 0 for r in results)
    print(f"  Status: {'PASS' if quality_ok else 'WARN'}")

    return {"queries": results, "avg_jaccard": avg_jaccard, "pass": quality_ok}


def validate_incremental_latency(codebase_name: str, codebase_path: str) -> dict:
    """Measure time for an incremental CocoIndex update (no-op or minimal delta)."""
    print("\n" + "=" * 60)
    print("3. INCREMENTAL LATENCY")
    print("=" * 60)

    if not codebase_path or not Path(codebase_path).exists():
        print(f"  ✗ Codebase path not found: {codebase_path}")
        return {"error": "path not found", "pass": False}

    try:
        import cocoindex as coco
        from waverider.cocoindex_app import make_app

        app = make_app(codebase_name, Path(codebase_path))

        start = time.time()

        import asyncio

        async def _update():
            async with coco.runtime():
                await app.update()

        asyncio.run(_update())
        elapsed = time.time() - start

        print(f"  Incremental update time: {elapsed:.2f}s")
        latency_ok = elapsed < 60.0  # Should complete no-op in under 60s
        print(f"  Target: <60s for no-op update")
        print(f"  Status: {'PASS' if latency_ok else 'WARN'}")

        return {"elapsed_seconds": elapsed, "pass": latency_ok}
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return {"error": str(e), "pass": False}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Corpus validation: CocoIndex vs legacy schema")
    parser.add_argument("--codebase-name", required=True, help="Codebase to validate")
    parser.add_argument(
        "--queries", nargs="*",
        default=["database connection", "error handling", "authentication"],
        help="Search queries for retrieval quality comparison",
    )
    parser.add_argument("--limit", type=int, default=10, help="Top-k limit for comparisons")
    parser.add_argument("--skip-latency", action="store_true", help="Skip incremental latency check")
    args = parser.parse_args()

    print("=" * 60)
    print(f"CORPUS VALIDATION: {args.codebase_name}")
    print("=" * 60)

    db = DatabaseManager()
    codebase = db.get_codebase(args.codebase_name)
    if not codebase:
        print(f"✗ Codebase '{args.codebase_name}' not registered in database")
        return 1

    codebase_id = codebase["id"]
    codebase_path = codebase.get("path", "")

    # Run validations
    count_result = validate_count_parity(db, args.codebase_name, codebase_id)
    quality_result = validate_retrieval_quality(
        db, args.codebase_name, codebase_id, args.queries, limit=args.limit,
    )

    latency_result = {"pass": True, "skipped": True}
    if not args.skip_latency:
        latency_result = validate_incremental_latency(args.codebase_name, codebase_path)

    db.close()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_pass = count_result["pass"] and quality_result["pass"] and latency_result["pass"]
    print(f"  Count parity:       {'PASS' if count_result['pass'] else 'WARN'}")
    print(f"  Retrieval quality:  {'PASS' if quality_result['pass'] else 'WARN'}")
    print(f"  Incremental latency: {'PASS' if latency_result['pass'] else 'WARN'}"
          f"{'  (skipped)' if latency_result.get('skipped') else ''}")
    print(f"\n  Overall: {'✓ ALL CHECKS PASSED' if all_pass else '⚠ SOME CHECKS NEED ATTENTION'}")
    print("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
