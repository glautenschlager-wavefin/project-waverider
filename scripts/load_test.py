#!/usr/bin/env python3
"""Phase 5, Step 17: Team-scale readiness / load test.

Simulates concurrent query load from multiple engineers hitting the
MCP search endpoint simultaneously. Measures latency percentiles
(p50/p95/p99) and throughput to verify production readiness.

Usage:
    python scripts/load_test.py --codebase-name waverider --concurrency 20
    python scripts/load_test.py --codebase-name identity --concurrency 50 --duration 60
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx

from waverider.database import DatabaseManager
from waverider.fusion import rrf_fuse


# ---------------------------------------------------------------------------
# Test queries (representative of real engineer usage)
# ---------------------------------------------------------------------------

DEFAULT_QUERIES = [
    "database connection pool",
    "authentication middleware",
    "error handling pattern",
    "user model schema",
    "API endpoint handler",
    "payment processing flow",
    "email notification service",
    "file upload handler",
    "caching strategy",
    "logging configuration",
    "webhook delivery",
    "rate limiting implementation",
    "search indexing pipeline",
    "background job worker",
    "configuration management",
]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def _embed_query_async(client: httpx.AsyncClient, query: str) -> list[float]:
    """Async embedding generation via Ollama."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "nomic-embed-text")
    resp = await client.post(
        f"{ollama_url}/api/embeddings",
        json={"model": model, "prompt": query},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


async def _run_search(
    db: DatabaseManager,
    client: httpx.AsyncClient,
    codebase_name: str,
    query: str,
    limit: int = 10,
) -> dict:
    """Execute a full hybrid search and return timing info."""
    start = time.perf_counter()

    try:
        vec = await _embed_query_async(client, query)
        embed_time = time.perf_counter() - start

        search_start = time.perf_counter()
        vector_results = db.search_coco_embeddings(vec, codebase_name, limit=limit * 2)
        keyword_results = db.search_coco_bm25(query, codebase_name, limit=limit * 2)

        if vector_results and keyword_results:
            results = rrf_fuse(
                {"vector": vector_results, "keyword": keyword_results},
                id_key="id",
                limit=limit,
            )
        else:
            results = vector_results[:limit]

        search_time = time.perf_counter() - search_start
        total_time = time.perf_counter() - start

        return {
            "query": query,
            "total_ms": total_time * 1000,
            "embed_ms": embed_time * 1000,
            "search_ms": search_time * 1000,
            "result_count": len(results),
            "success": True,
        }
    except Exception as e:
        total_time = time.perf_counter() - start
        return {
            "query": query,
            "total_ms": total_time * 1000,
            "error": str(e),
            "success": False,
        }


async def _worker(
    worker_id: int,
    db: DatabaseManager,
    client: httpx.AsyncClient,
    codebase_name: str,
    queries: list[str],
    results: list,
    duration: float,
    semaphore: asyncio.Semaphore,
):
    """Individual concurrent worker running queries in a loop."""
    start_time = time.perf_counter()
    query_idx = worker_id % len(queries)

    while (time.perf_counter() - start_time) < duration:
        query = queries[query_idx % len(queries)]
        async with semaphore:
            result = await _run_search(db, client, codebase_name, query)
        results.append(result)
        query_idx += 1
        # Small jitter between requests
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Main load test
# ---------------------------------------------------------------------------

async def run_load_test(
    codebase_name: str,
    concurrency: int,
    duration: float,
    queries: list[str],
) -> dict:
    """Run concurrent search load test."""
    print(f"\nLoad Test Configuration:")
    print(f"  Codebase:    {codebase_name}")
    print(f"  Concurrency: {concurrency} workers")
    print(f"  Duration:    {duration}s")
    print(f"  Queries:     {len(queries)} unique")
    print()

    db = DatabaseManager()

    # Verify coco_snippets table exists
    if not db.coco_table_exists():
        print("✗ coco_snippets table does not exist. Run CocoIndex indexer first.")
        db.close()
        return {"pass": False, "error": "no coco_snippets table"}

    results: list[dict] = []
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        # Warm-up: single query to prime connections
        print("Warming up...")
        warmup = await _run_search(db, client, codebase_name, queries[0])
        if not warmup["success"]:
            print(f"✗ Warm-up failed: {warmup.get('error')}")
            db.close()
            return {"pass": False, "error": warmup.get("error")}
        print(f"  Warm-up query: {warmup['total_ms']:.0f}ms ({warmup['result_count']} results)")

        # Run load test
        print(f"\nRunning {concurrency} concurrent workers for {duration}s...")
        start = time.perf_counter()

        workers = [
            _worker(i, db, client, codebase_name, queries, results, duration, semaphore)
            for i in range(concurrency)
        ]
        await asyncio.gather(*workers)

        wall_time = time.perf_counter() - start

    db.close()

    # Analyze results
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    if not successful:
        print("✗ All queries failed!")
        return {"pass": False, "error": "all queries failed"}

    total_times = [r["total_ms"] for r in successful]
    search_times = [r["search_ms"] for r in successful]
    embed_times = [r["embed_ms"] for r in successful]

    total_times.sort()
    search_times.sort()

    def percentile(data: list[float], p: float) -> float:
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    report = {
        "total_queries": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "wall_time_s": wall_time,
        "throughput_qps": len(successful) / wall_time,
        "total_p50_ms": percentile(total_times, 50),
        "total_p95_ms": percentile(total_times, 95),
        "total_p99_ms": percentile(total_times, 99),
        "search_p50_ms": percentile(search_times, 50),
        "search_p95_ms": percentile(search_times, 95),
        "search_p99_ms": percentile(search_times, 99),
        "embed_avg_ms": statistics.mean(embed_times),
        "avg_results_per_query": statistics.mean(r["result_count"] for r in successful),
    }

    # Print report
    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS")
    print("=" * 60)
    print(f"  Total queries:      {report['total_queries']}")
    print(f"  Successful:         {report['successful']}")
    print(f"  Failed:             {report['failed']}")
    print(f"  Wall time:          {report['wall_time_s']:.1f}s")
    print(f"  Throughput:         {report['throughput_qps']:.1f} queries/sec")
    print()
    print("  Latency (total = embed + search):")
    print(f"    p50: {report['total_p50_ms']:.0f}ms")
    print(f"    p95: {report['total_p95_ms']:.0f}ms")
    print(f"    p99: {report['total_p99_ms']:.0f}ms")
    print()
    print("  Search-only latency (DB queries):")
    print(f"    p50: {report['search_p50_ms']:.0f}ms")
    print(f"    p95: {report['search_p95_ms']:.0f}ms")
    print(f"    p99: {report['search_p99_ms']:.0f}ms")
    print()
    print(f"  Embedding avg:      {report['embed_avg_ms']:.0f}ms")
    print(f"  Avg results/query:  {report['avg_results_per_query']:.1f}")
    print()

    # Pass/fail criteria
    search_p95_ok = report["search_p95_ms"] < 500  # <500ms for DB search
    total_p95_ok = report["total_p95_ms"] < 5000   # <5s total (includes Ollama)
    error_rate_ok = report["failed"] / max(report["total_queries"], 1) < 0.05

    print("  Acceptance Criteria:")
    print(f"    Search p95 < 500ms:  {'PASS' if search_p95_ok else 'FAIL'} ({report['search_p95_ms']:.0f}ms)")
    print(f"    Total p95 < 5000ms:  {'PASS' if total_p95_ok else 'FAIL'} ({report['total_p95_ms']:.0f}ms)")
    print(f"    Error rate < 5%:     {'PASS' if error_rate_ok else 'FAIL'} "
          f"({report['failed'] / max(report['total_queries'], 1) * 100:.1f}%)")

    report["pass"] = search_p95_ok and total_p95_ok and error_rate_ok
    print(f"\n  Overall: {'✓ PASS' if report['pass'] else '✗ FAIL'}")
    print("=" * 60)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Team-scale load test for Waverider search")
    parser.add_argument("--codebase-name", required=True, help="Codebase to test against")
    parser.add_argument("--concurrency", type=int, default=20, help="Number of concurrent workers")
    parser.add_argument("--duration", type=float, default=30.0, help="Test duration in seconds")
    parser.add_argument("--queries", nargs="*", default=None, help="Custom queries (uses defaults if omitted)")
    args = parser.parse_args()

    queries = args.queries if args.queries else DEFAULT_QUERIES

    report = asyncio.run(run_load_test(
        codebase_name=args.codebase_name,
        concurrency=args.concurrency,
        duration=args.duration,
        queries=queries,
    ))

    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
