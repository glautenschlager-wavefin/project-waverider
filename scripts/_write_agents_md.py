#!/usr/bin/env python3
"""Write the updated AGENTS.md file."""
from pathlib import Path
import os

os.chdir(Path(__file__).resolve().parent.parent)

content = (
    "# Agent Configuration for Waverider\n"
    "\n"
    "## Available MCP Tools\n"
    "\n"
    "This workspace exposes the following MCP (Model Context Protocol) tools via the **Waverider** server:\n"
    "\n"
    "### 1. `search`\n"
    "**Purpose**: Hybrid code search combining BM25 keyword matching and vector semantic search, fused with Reciprocal Rank Fusion (RRF)\n"
    "**When to use**: Finding code by name, keyword, concept, behavior, or design intent\n"
    "**Returns**: **Full source code** of matching functions/classes/methods, with file paths, line numbers, RRF scores, and structural context from the knowledge graph\n"
    "**Parameters**:\n"
    '- `query` (string): What to search for -- exact identifiers (e.g., "DatabaseManager") **and** semantic descriptions (e.g., "how embeddings are generated")\n'
    '- `codebase_name` (string): Name of the indexed codebase (default: "waverider")\n'
    "- `limit` (integer, optional): Max results to return (default: 10)\n"
    "- `alpha` (float, optional): Balance between BM25 and vector search (default: 0.5).\n"
    "  - `0.0` = pure BM25 (keyword only)\n"
    "  - `1.0` = pure vector (semantic only)\n"
    "  - `0.5` = equal weight (default, recommended for most queries)\n"
    "  - **Guidance**: Increase toward 1.0 for conceptual/semantic queries. Decrease toward 0.0 for exact identifier lookups.\n"
    "\n"
    "**Example invocations**:\n"
    "```\n"
    'search(query="DatabaseManager", codebase_name="waverider", limit=5)\n'
    'search(query="how snippets are extracted and indexed", codebase_name="waverider", limit=5)\n'
    'search(query="add_embedding", codebase_name="waverider", alpha=0.2)\n'
    "```\n"
    "\n"
    "---\n"
    "\n"
    "### 2. `explore_graph`\n"
    "**Purpose**: Structural traversal of the knowledge graph -- navigate relationships from a known entity\n"
    "**When to use**: When you already have a starting point and want to explore its relationships (callers, callees, methods, imports, file inventory)\n"
    "**Returns**: Relationship information only (no source code). Use `search` to retrieve code for entities found via exploration.\n"
    "**Parameters**:\n"
    "- `entity_name` (string): Name of the function, class, or file to explore\n"
    '- `codebase_name` (string): Name of the indexed codebase (default: "waverider")\n'
    '- `relationship` (string, optional): One of: `"callers"`, `"callees"`, `"methods"`, `"imports"`, `"all"` (default)\n'
    "\n"
    "**Example invocations**:\n"
    "```\n"
    'explore_graph(entity_name="DatabaseManager", codebase_name="waverider", relationship="methods")\n'
    'explore_graph(entity_name="search_embeddings", codebase_name="waverider", relationship="callers")\n'
    "```\n"
    "\n"
    "---\n"
    "\n"
    "## Agent Decision Tree\n"
    "\n"
    "**Use Waverider tools when:**\n"
    '- User asks "How does [class/function] work?" -> Use `search` with default alpha\n'
    '- User asks "How is [behavior] implemented?" -> Use `search` with `alpha=0.7` (semantic-leaning)\n'
    '- User asks "Find [exact function name]" -> Use `search` with `alpha=0.2` (keyword-leaning)\n'
    '- User asks "What calls function X?" or "Show methods of class Y" -> Use `explore_graph`\n'
    "- User asks for code examples -> Use `search` with semantic query\n"
    "- User asks about architecture/design -> Use `search` then `explore_graph` for call graphs\n"
    "\n"
    "**After receiving tool results:**\n"
    "- If results contain relevant code snippets -> **answer directly from the snippets**. Do NOT read the original source files.\n"
    "- If results are incomplete -> read only the specific files/lines that are missing.\n"
    "- `search` returns **full function/method bodies** -- sufficient for implementation questions.\n"
    "- `explore_graph` returns **structural relationships** -- use to discover related entities, then `search` for their code.\n"
    "\n"
    "**Do NOT use Waverider when:**\n"
    "- User asks setup/installation questions -> Use README\n"
    "- User asks project metadata (authors, license) -> Use README or config files\n"
    "- User asks theoretical CS questions -> Answer directly\n"
    "- Question is already answerable from editing context (open file)\n"
    "\n"
    "---\n"
    "\n"
    "## Embedding Quality Notes\n"
    "\n"
    "- Embeddings are generated using **Ollama** (local, offline model: nomic-embed-text)\n"
    "- Snippets are extracted via **tree-sitter** (multi-language) or **Python AST** fallback\n"
    "- Class snippets include declaration + docstring + method signature list (methods indexed separately)\n"
    "- BM25 keyword search uses **SQLite FTS5** with code-aware tokenization (snake_case, camelCase, dot.path splitting)\n"
    "- Results are fused with **Reciprocal Rank Fusion** (k=60)\n"
    "\n"
    "---\n"
    "\n"
    "## Alpha Tuning Notes\n"
    "\n"
    "The `alpha` parameter controls BM25 vs vector weighting in RRF:\n"
    "- Default `0.5` works well for mixed queries\n"
    "- For **exact identifiers** (function names, class names, error messages): use `alpha=0.0-0.3`\n"
    '- For **conceptual queries** ("how does X work", "pattern for Y"): use `alpha=0.7-1.0`\n'
    "- After launch, monitor which alpha values agents tend to use. If agents consistently override the default, adjust it.\n"
    "\n"
    "---\n"
    "\n"
    "## Troubleshooting\n"
    "\n"
    "**Tools not appearing in suggestions?**\n"
    "- Ensure `.vscode/mcp.json` exists and defines the waverider server\n"
    "- Restart VS Code window to reload MCP servers\n"
    "\n"
    "**Search returning no results?**\n"
    "- For keyword search (BM25), ensure the FTS5 index is populated: run `python scripts/backfill_fts.py`\n"
    "- For semantic search, check that the codebase has been indexed (run `make index`)\n"
    '- Verify codebase_name is exactly "waverider"\n'
    "\n"
    "**Getting stale results?**\n"
    "- Rebuild index with `make index` after code changes\n"
    "- Run `python scripts/backfill_fts.py` to update FTS5 index for existing codebases\n"
    "- Neo4j graph enrichment is best-effort -- search works without it\n"
)

Path("AGENTS.md").write_text(content)
print("Done")
