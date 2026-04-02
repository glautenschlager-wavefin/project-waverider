# Agent Configuration for Waverider

## Available MCP Tools

This workspace exposes the following MCP (Model Context Protocol) tools via the **Waverider** server:

### 1. `search`
**Purpose**: Hybrid code search combining BM25 keyword matching and vector semantic search, fused with Reciprocal Rank Fusion (RRF)
**When to use**: Finding code by name, keyword, concept, behavior, or design intent
**Returns**: **Full source code** of matching functions/classes/methods, with file paths, line numbers, RRF scores, and structural context from the knowledge graph
**Parameters**:
- `query` (string): What to search for -- exact identifiers (e.g., "DatabaseManager") **and** semantic descriptions (e.g., "how embeddings are generated")
- `codebase_name` (string): Name of the indexed codebase (default: "waverider")
- `limit` (integer, optional): Max results to return (default: 10)
- `alpha` (float, optional): Balance between BM25 and vector search (default: 0.5).
  - `0.0` = pure BM25 (keyword only)
  - `1.0` = pure vector (semantic only)
  - `0.5` = equal weight (default, recommended for most queries)
  - **Guidance**: Increase toward 1.0 for conceptual/semantic queries. Decrease toward 0.0 for exact identifier lookups.

**Example invocations**:
```
search(query="DatabaseManager", codebase_name="waverider", limit=5)
search(query="how snippets are extracted and indexed", codebase_name="waverider", limit=5)
search(query="add_embedding", codebase_name="waverider", alpha=0.2)
```

---

### 2. `explore_graph`
**Purpose**: Structural traversal of the knowledge graph -- navigate relationships from a known entity
**When to use**: When you already have a starting point and want to explore its relationships (callers, callees, methods, imports, file inventory)
**Returns**: Relationship information only (no source code). Use `search` to retrieve code for entities found via exploration.
**Parameters**:
- `entity_name` (string): Name of the function, class, or file to explore
- `codebase_name` (string): Name of the indexed codebase (default: "waverider")
- `relationship` (string, optional): One of: `"callers"`, `"callees"`, `"methods"`, `"imports"`, `"all"` (default)

**Example invocations**:
```
explore_graph(entity_name="DatabaseManager", codebase_name="waverider", relationship="methods")
explore_graph(entity_name="search_embeddings", codebase_name="waverider", relationship="callers")
```

---

## Agent Decision Tree

Default stance: for code-understanding tasks in Wave services, use Waverider before manual local file search.

**Use Waverider tools when:**
- User asks "How does [class/function] work?" -> Use `search` with default alpha
- User asks "How is [behavior] implemented?" -> Use `search` with `alpha=0.7` (semantic-leaning)
- User asks "Find [exact function name]" -> Use `search` with `alpha=0.2` (keyword-leaning)
- User asks "What calls function X?" or "Show methods of class Y" -> Use `explore_graph`
- User asks for code examples -> Use `search` with semantic query
- User asks about architecture/design -> Use `search` then `explore_graph` for call graphs
- User asks for an end-to-end flow trace -> Use `search` then `explore_graph` for callers/callees

**After receiving tool results:**
- If results contain relevant code snippets -> **answer directly from the snippets**. Do NOT read the original source files.
- If results are incomplete -> read only the specific files/lines that are missing.
- `search` returns **full function/method bodies** -- sufficient for implementation questions.
- `explore_graph` returns **structural relationships** -- use to discover related entities, then `search` for their code.

**Do NOT use Waverider when:**
- User asks setup/installation questions -> Use README
- User asks project metadata (authors, license) -> Use README or config files
- User asks theoretical CS questions -> Answer directly

Important: "The file is already open" is not sufficient justification to skip Waverider for implementation/flow analysis.

---

## Embedding Quality Notes

- Embeddings are generated using **Ollama** (local, offline model: nomic-embed-text)
- Snippets are extracted via **tree-sitter** (multi-language) or **Python AST** fallback
- Class snippets include declaration + docstring + method signature list (methods indexed separately)
- BM25 keyword search uses **SQLite FTS5** with code-aware tokenization (snake_case, camelCase, dot.path splitting)
- Results are fused with **Reciprocal Rank Fusion** (k=60)

---

## Alpha Tuning Notes

The `alpha` parameter controls BM25 vs vector weighting in RRF:
- Default `0.5` works well for mixed queries
- For **exact identifiers** (function names, class names, error messages): use `alpha=0.0-0.3`
- For **conceptual queries** ("how does X work", "pattern for Y"): use `alpha=0.7-1.0`
- After launch, monitor which alpha values agents tend to use. If agents consistently override the default, adjust it.

---

## Troubleshooting

**Tools not appearing in suggestions?**
- Ensure `.vscode/mcp.json` exists and defines the waverider server
- Restart VS Code window to reload MCP servers

**Search returning no results?**
- For keyword search (BM25), ensure the FTS5 index is populated: run `python scripts/backfill_fts.py`
- For semantic search, check that the codebase has been indexed (run `make index`)
- Verify codebase_name is exactly "waverider"

**Getting stale results?**
- Rebuild index with `make index` after code changes
- Run `python scripts/backfill_fts.py` to update FTS5 index for existing codebases
- Neo4j graph enrichment is best-effort -- search works without it
