# Agent Configuration for Waverider

## Available MCP Tools

This workspace exposes the following MCP (Model Context Protocol) tools via the **Waverider** server:

### 1. `search_codebase`
**Purpose**: Keyword-based code search using Neo4j graph relationships  
**When to use**: Finding code by explicit names, classes, functions, or identifiers  
**Returns**: File paths, matching function **signatures with docstrings**, matching class **names with docstrings**, and lists of all functions/classes per file  
**Parameters**:
- `query` (string): Search term (e.g., "DatabaseManager", "add_embedding")
- `codebase_name` (string): Should be "waverider"
- `limit` (integer, optional): Max results to return (default: 10)

**Example invocation**:
```
search_codebase(query="DatabaseManager", codebase_name="waverider", limit=3)
```

---

### 2. `retrieve_code`
**Purpose**: Semantic code search using embeddings and cosine similarity  
**When to use**: Finding code by concept, behavior, or design intent (not explicit names)  
**Returns**: **Full source code** of matching functions/classes/methods, with docstrings, line numbers, file paths, and similarity scores  
**Parameters**:
- `query` (string): Semantic description (e.g., "how to index a codebase", "embedding generation")
- `codebase_name` (string): Should be "waverider"
- `limit` (integer, optional): Max results to return (default: 5)

**Example invocation**:
```
retrieve_code(query="how snippets are extracted and indexed", codebase_name="waverider", limit=5)
```

---

## Agent Decision Tree

**Use Waverider tools when:**
- User asks "How does [class/function] work?" → Use `search_codebase` first, then `retrieve_code` for details
- User asks "How is [behavior] implemented?" → Use `retrieve_code`
- User asks "What's the relationship between X and Y?" → Use `search_codebase` on both, then `retrieve_code` for design patterns
- User asks for code examples → Use `retrieve_code` with semantic query
- User asks about architecture/design → Use both tools to show implementation

**After receiving tool results:**
- If results contain relevant code snippets → **answer directly from the snippets**. Do NOT read the original source files.
- If results are incomplete or don't cover the question → read only the specific files/lines that are missing.
- `retrieve_code` returns **full function/method bodies** — these are sufficient to answer implementation questions.
- `search_codebase` returns **signatures and docstrings** — these are sufficient to answer structural/discovery questions.

**Do NOT use Waverider when:**
- User asks setup/installation questions → Use README
- User asks project metadata (authors, license) → Use README or config files
- User asks theoretical CS questions → Answer directly
- Question is already answerable from editing context (open file)

---

## Embedding Quality Notes

- Embeddings are generated using **Ollama** (local, offline model: nomic-embed-text)
- Snippets are extracted via **Python AST** (functions, classes with method signature summaries, module constants, imports)
- Class snippets include declaration + docstring + method signature list (no full method bodies — those are indexed separately)
- Context window target: ~8K tokens for semantic search accuracy

---

## Troubleshooting

**Tools not appearing in suggestions?**
- Ensure `.vscode/mcp.json` exists and defines the waverider server
- Restart VS Code window to reload MCP servers
- Check that Neo4j and the MCP server are running

**Search returning no results?**
- Try `search_codebase` first with explicit names (more reliable)
- For semantic search, check that the codebase has been indexed (run `make index`)
- Verify codebase_name is exactly "waverider"

**Getting stale results?**
- Rebuild index with `make index` after code changes
- Neo4j graph is independent from SQLite; both are updated together during indexing
