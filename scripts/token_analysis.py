#!/usr/bin/env python3
"""
Token Savings Analysis for Waverider.

Computes how many context tokens Waverider saves compared to naive file reading,
scaled across the org, and projects annual cost savings.

Usage:
    python scripts/token_analysis.py [--engineers N] [--queries-per-day N] [--working-days N] [--db-path PATH]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4  # Standard GPT tokenizer approximation

# Empirical data from Waverider test_results/ (Test 4 vs Control 4)
WITHOUT_WR_FILES_PER_QUERY = 12       # Avg full files read per code question
WITHOUT_WR_TOOL_CALLS = 70            # Avg filesystem operations (grep, find, read_file, list_dir)
WITHOUT_WR_FAILED_SEARCHES = 20       # Empty/wasted searches per query
WITHOUT_WR_DUPLICATE_READS = 5        # Files read more than once
REREAD_MULTIPLIER = 1.5               # Conservative multiplier for duplicate file reads

WITH_WR_TOOL_CALLS = 5                # Avg MCP tool calls per query
WITH_WR_SNIPPETS_PER_QUERY = 10       # Default limit for search results

# Reasoning token waste per failed/empty search operation
REASONING_TOKENS_PER_FAILED_OP = 300  # Tool call overhead + agent deliberation + retry logic

# Token pricing (per million input tokens)
PRICING = {
    "GPT-4o":       2.50,
    "Claude Opus":  15.00,
    "Claude Sonnet": 3.00,
}


def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def query_codebase_stats(conn: sqlite3.Connection) -> list[dict]:
    """Get per-codebase file and snippet statistics."""
    rows = conn.execute("""
        SELECT
            cm.id                                       AS codebase_id,
            cm.name                                     AS codebase_name,
            COUNT(DISTINCT sf.id)                       AS file_count,
            COALESCE(SUM(sf.file_size), 0)              AS total_file_bytes,
            COUNT(cs.id)                                 AS snippet_count,
            COALESCE(SUM(LENGTH(cs.content)), 0)         AS total_snippet_chars
        FROM codebase_metadata cm
        LEFT JOIN source_files sf ON sf.codebase_id = cm.id
        LEFT JOIN code_snippets cs ON cs.file_id = sf.id
        GROUP BY cm.id
        ORDER BY total_file_bytes DESC
    """).fetchall()
    return [dict(r) for r in rows]


def query_avg_snippet_size(conn: sqlite3.Connection, codebase_id: int) -> float:
    """Average snippet size in characters for a codebase."""
    row = conn.execute("""
        SELECT AVG(LENGTH(cs.content)) AS avg_chars
        FROM code_snippets cs
        JOIN source_files sf ON cs.file_id = sf.id
        WHERE sf.codebase_id = ?
    """, (codebase_id,)).fetchone()
    return row["avg_chars"] or 0.0


def query_avg_file_size(conn: sqlite3.Connection, codebase_id: int) -> float:
    """Average file size in bytes for a codebase."""
    row = conn.execute("""
        SELECT AVG(file_size) AS avg_bytes
        FROM source_files
        WHERE codebase_id = ? AND file_size > 0
    """, (codebase_id,)).fetchone()
    return row["avg_bytes"] or 0.0


def to_tokens(chars: float) -> int:
    return int(chars / CHARS_PER_TOKEN)


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_dollars(amount: float) -> str:
    if amount >= 1_000:
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


# ── Analysis ─────────────────────────────────────────────────────────────────

def run_analysis(db_path: str, engineers: int, queries_per_day: int, working_days: int):
    conn = get_db_connection(db_path)
    stats = query_codebase_stats(conn)

    if not stats:
        print("No codebases found in the database.")
        sys.exit(1)

    # ── Phase 1: Per-repo static metrics ─────────────────────────────────
    print()
    print("=" * 90)
    print("  WAVERIDER TOKEN SAVINGS ANALYSIS")
    print("=" * 90)

    print()
    print("Phase 1: Per-Codebase Metrics")
    print("-" * 90)
    print(f"  {'Codebase':<22} {'Files':>7} {'File Tokens':>13} {'Snippets':>9} "
          f"{'Snippet Tokens':>15} {'Compression':>12}")
    print(f"  {'─' * 22} {'─' * 7} {'─' * 13} {'─' * 9} {'─' * 15} {'─' * 12}")

    total_file_tokens = 0
    total_snippet_tokens = 0
    total_files = 0
    total_snippets = 0
    per_repo = []

    for s in stats:
        file_tokens = to_tokens(s["total_file_bytes"])
        snippet_tokens = to_tokens(s["total_snippet_chars"])
        compression = file_tokens / snippet_tokens if snippet_tokens > 0 else 0
        avg_file_tokens = to_tokens(query_avg_file_size(conn, s["codebase_id"]))
        avg_snippet_tokens = to_tokens(query_avg_snippet_size(conn, s["codebase_id"]))

        total_file_tokens += file_tokens
        total_snippet_tokens += snippet_tokens
        total_files += s["file_count"]
        total_snippets += s["snippet_count"]

        per_repo.append({
            "name": s["codebase_name"],
            "file_count": s["file_count"],
            "file_tokens": file_tokens,
            "snippet_count": s["snippet_count"],
            "snippet_tokens": snippet_tokens,
            "compression": compression,
            "avg_file_tokens": avg_file_tokens,
            "avg_snippet_tokens": avg_snippet_tokens,
        })

        print(f"  {s['codebase_name']:<22} {s['file_count']:>7,} {format_tokens(file_tokens):>13} "
              f"{s['snippet_count']:>9,} {format_tokens(snippet_tokens):>15} "
              f"{compression:>11.1f}x")

    overall_compression = total_file_tokens / total_snippet_tokens if total_snippet_tokens > 0 else 0
    print(f"  {'─' * 22} {'─' * 7} {'─' * 13} {'─' * 9} {'─' * 15} {'─' * 12}")
    print(f"  {'TOTAL':<22} {total_files:>7,} {format_tokens(total_file_tokens):>13} "
          f"{total_snippets:>9,} {format_tokens(total_snippet_tokens):>15} "
          f"{overall_compression:>11.1f}x")

    # ── Phase 2: Per-query model ─────────────────────────────────────────
    # Compute average across all repos (weighted by file count)
    weighted_avg_file = sum(r["avg_file_tokens"] * r["file_count"] for r in per_repo) / total_files if total_files else 0
    weighted_avg_snippet = sum(r["avg_snippet_tokens"] * r["snippet_count"] for r in per_repo) / total_snippets if total_snippets else 0

    # Without Waverider: read N full files, with duplicate reads
    without_wr_context = int(WITHOUT_WR_FILES_PER_QUERY * weighted_avg_file * REREAD_MULTIPLIER)
    without_wr_reasoning_waste = WITHOUT_WR_FAILED_SEARCHES * REASONING_TOKENS_PER_FAILED_OP
    without_wr_total = without_wr_context + without_wr_reasoning_waste

    # With Waverider: load N snippets, no waste
    with_wr_context = int(WITH_WR_SNIPPETS_PER_QUERY * weighted_avg_snippet)
    with_wr_reasoning_waste = 0  # No failed searches
    with_wr_total = with_wr_context + with_wr_reasoning_waste

    savings_per_query = without_wr_total - with_wr_total

    print()
    print()
    print("Phase 2: Per-Query Token Model")
    print("-" * 90)
    print()
    print(f"  Weighted avg file size:    {format_tokens(int(weighted_avg_file))} tokens")
    print(f"  Weighted avg snippet size: {format_tokens(int(weighted_avg_snippet))} tokens")
    print()
    print(f"  {'Metric':<40} {'Without WR':>14} {'With WR':>14} {'Savings':>14}")
    print(f"  {'─' * 40} {'─' * 14} {'─' * 14} {'─' * 14}")
    print(f"  {'Files/snippets loaded per query':<40} "
          f"{WITHOUT_WR_FILES_PER_QUERY:>14} {WITH_WR_SNIPPETS_PER_QUERY:>14} {'':>14}")
    print(f"  {'Context tokens loaded':<40} "
          f"{format_tokens(without_wr_context):>14} {format_tokens(with_wr_context):>14} "
          f"{format_tokens(without_wr_context - with_wr_context):>14}")
    print(f"  {'Wasted reasoning tokens (failed ops)':<40} "
          f"{format_tokens(without_wr_reasoning_waste):>14} {with_wr_reasoning_waste:>14} "
          f"{format_tokens(without_wr_reasoning_waste):>14}")
    print(f"  {'─' * 40} {'─' * 14} {'─' * 14} {'─' * 14}")
    print(f"  {'TOTAL tokens per query':<40} "
          f"{format_tokens(without_wr_total):>14} {format_tokens(with_wr_total):>14} "
          f"{format_tokens(savings_per_query):>14}")
    print()
    print(f"  Reduction per query: {savings_per_query / without_wr_total * 100:.0f}% "
          f"({without_wr_total / with_wr_total:.0f}x fewer tokens)")

    # ── Phase 3: Org-wide projection ─────────────────────────────────────
    annual_queries = engineers * queries_per_day * working_days
    annual_savings_tokens = annual_queries * savings_per_query
    annual_without = annual_queries * without_wr_total
    annual_with = annual_queries * with_wr_total

    print()
    print()
    print("Phase 3: Annual Org-Wide Projection")
    print("-" * 90)
    print()
    print(f"  Parameters:")
    print(f"    Engineers:        {engineers}")
    print(f"    Queries/day:      {queries_per_day}")
    print(f"    Working days/yr:  {working_days}")
    print(f"    Total queries/yr: {annual_queries:,}")
    print()
    print(f"  {'Metric':<40} {'Without WR':>14} {'With WR':>14} {'Savings':>14}")
    print(f"  {'─' * 40} {'─' * 14} {'─' * 14} {'─' * 14}")
    print(f"  {'Annual input tokens':<40} "
          f"{format_tokens(annual_without):>14} {format_tokens(annual_with):>14} "
          f"{format_tokens(annual_savings_tokens):>14}")

    print()
    print(f"  Estimated annual cost savings by model:")
    print()
    for model, price_per_m in PRICING.items():
        cost_without = annual_without / 1_000_000 * price_per_m
        cost_with = annual_with / 1_000_000 * price_per_m
        cost_saved = annual_savings_tokens / 1_000_000 * price_per_m
        print(f"    {model:<16} "
              f"Without: {format_dollars(cost_without):>10}  "
              f"With: {format_dollars(cost_with):>10}  "
              f"Saved: {format_dollars(cost_saved):>10}")

    # ── Sensitivity table ────────────────────────────────────────────────
    engineer_scenarios = [50, 100, 150]
    query_scenarios = [5, 10, 20]

    print()
    print()
    print("Sensitivity Analysis: Annual Token Savings")
    print("-" * 90)
    print()

    # Header
    header = f"  {'':>20}"
    for q in query_scenarios:
        header += f" {q} queries/day  "
    print(header)
    print(f"  {'':>20}" + " ─────────────  " * len(query_scenarios))

    for eng in engineer_scenarios:
        row = f"  {eng:>3} engineers      "
        for q in query_scenarios:
            total = eng * q * working_days * savings_per_query
            row += f" {format_tokens(total):>13}  "
        print(row)

    # Same table in dollars (Claude Sonnet pricing as middle ground)
    ref_model = "Claude Sonnet"
    ref_price = PRICING[ref_model]

    print()
    print(f"  Annual cost savings ({ref_model} @ ${ref_price}/M input tokens):")
    print()

    header = f"  {'':>20}"
    for q in query_scenarios:
        header += f" {q} queries/day  "
    print(header)
    print(f"  {'':>20}" + " ─────────────  " * len(query_scenarios))

    for eng in engineer_scenarios:
        row = f"  {eng:>3} engineers      "
        for q in query_scenarios:
            total = eng * q * working_days * savings_per_query
            cost = total / 1_000_000 * ref_price
            row += f" {format_dollars(cost):>13}  "
        print(row)

    # ── Methodology notes ────────────────────────────────────────────────
    print()
    print()
    print("Methodology & Assumptions")
    print("-" * 90)
    print(f"""
  Token estimation:     1 token ≈ {CHARS_PER_TOKEN} characters (standard GPT tokenizer approximation)
  File sizes:           From source_files.file_size in the Waverider index DB
  Snippet sizes:        From LENGTH(code_snippets.content) in the Waverider index DB
  Compression ratio:    Total file tokens / total snippet tokens per codebase

  Without Waverider (empirical, from test_results/analysis3and4.md):
    - ~{WITHOUT_WR_FILES_PER_QUERY} full files read per code question
    - ~{WITHOUT_WR_TOOL_CALLS} filesystem operations (grep, find, read_file, list_dir)
    - ~{WITHOUT_WR_FAILED_SEARCHES} failed/empty searches per query
    - ~{WITHOUT_WR_DUPLICATE_READS} duplicate file reads → {REREAD_MULTIPLIER}x re-read multiplier
    - ~{REASONING_TOKENS_PER_FAILED_OP} reasoning tokens wasted per failed operation

  With Waverider:
    - ~{WITH_WR_TOOL_CALLS} MCP tool calls per query
    - ~{WITH_WR_SNIPPETS_PER_QUERY} snippets returned (default search limit)
    - 0 failed searches, 0 duplicate reads

  These estimates are conservative. Actual savings are likely higher because:
    - Agents often read files multiple times across a conversation (not just per query)
    - Failed searches trigger retry loops with additional reasoning overhead
    - The "wrong codebase" problem (Control 4) wastes entire search phases
    - Snippet search returns only relevant code, not boilerplate/imports/tests
""")

    print("=" * 90)
    print()

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Waverider Token Savings Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--engineers", type=int, default=100,
                        help="Number of active Copilot users (default: 100)")
    parser.add_argument("--queries-per-day", type=int, default=8,
                        help="Avg code questions per engineer per day (default: 8)")
    parser.add_argument("--working-days", type=int, default=250,
                        help="Working days per year (default: 250)")
    parser.add_argument("--db-path", default="data/waverider.db",
                        help="Path to Waverider SQLite database (default: data/waverider.db)")

    args = parser.parse_args()

    db = Path(args.db_path)
    if not db.exists():
        print(f"Database not found: {db}")
        print("Run indexing first, or specify --db-path")
        sys.exit(1)

    run_analysis(
        db_path=str(db),
        engineers=args.engineers,
        queries_per_day=args.queries_per_day,
        working_days=args.working_days,
    )


if __name__ == "__main__":
    main()
