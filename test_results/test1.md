

    

Ran `retrieve_code` 
Completed with input: {
  "query": "embedding generation and storage pipeline",
  "codebase_name": "waverider",
  "limit": 5
}

Ran `retrieve_code` 
Completed with input: {
  "query": "retrieve_code similarity search cosine algorithm",
  "codebase_name": "waverider",
  "limit": 5
}

Ran `search_codebase` 
Completed with input: {
  "query": "Neo4j SQLite database",
  "codebase_name": "waverider",
  "limit": 10
}

Ran `retrieve_code` 
Completed with input: {
  "query": "how snippets are extracted from Python files and class snippet structure",
  "codebase_name": "waverider",
  "limit": 5
}

Ran `search_codebase` 
Completed with input: {
  "codebase_name": "waverider",
  "limit": 10,
  "query": "neo4j_graph"
}

Read [](file:///Users/glautenschlager/Library/Application%20Support/Code/User/workspaceStorage/0af567782b14d153f43603ea9b250312/GitHub.copilot-chat/chat-session-resources/9b999735-eec2-466c-a32e-78297a22383f/toolu_bdrk_01HU3C6cSwZn4SLvnk2nsTar__vscode-1774446282149/content.json)

Ran `retrieve_code` 
Completed with input: {
  "codebase_name": "waverider",
  "limit": 5,
  "query": "Neo4j graph indexing nodes relationships"
}

Read [](file:///Users/glautenschlager/Library/Application%20Support/Code/User/workspaceStorage/0af567782b14d153f43603ea9b250312/GitHub.copilot-chat/chat-session-resources/9b999735-eec2-466c-a32e-78297a22383f/toolu_bdrk_01HNMxEcNNgQ661eDZykBxis__vscode-1774446282151/content.json)

Ran `retrieve_code` 
Completed with input: {
  "codebase_name": "waverider",
  "limit": 3,
  "query": "Neo4j add_function add_class add_code_file graph nodes"
}

Ran `retrieve_code` 
Completed with input: {
  "codebase_name": "waverider",
  "limit": 3,
  "query": "index_codebase full pipeline storing snippets embeddings files"
}

Here's the end-to-end walkthrough:

---

## 1. Snippet Extraction

The entry point is `CodebaseIndexer.index_codebase()` in indexer.py. It walks every file returned by `get_files_to_index()` and calls `extract_snippets()`, which dispatches to `extract_python_snippets()` for `.py` files (non-Python files are indexed as a single `file` snippet).

**Python snippet types** (extracted via `ast` parsing):

| Type | Source AST node | What's captured |
|---|---|---|
| `function` | `ast.FunctionDef` | Full function body (including methods — methods are visited independently by `ast.walk`) |
| `class` | `ast.ClassDef` | **Compact** — see below |
| `import` | `ast.Import` / `ast.ImportFrom` | The import statement |
| `module_constant` | Top-level `ast.Assign` / `ast.AnnAssign` | Module-level assignments |
| `file` | fallback | Whole file, only if no other snippets were found or parsing failed |

**Class snippet structure** — intentionally compact ("table of contents"):
- Decorators + class declaration line
- Docstring (if present)
- A `# Methods:` block listing one-line signatures of each method

Full method bodies are **not** included in the class snippet — they're indexed separately as `function` snippets. A test in test_indexer.py (`test_extract_python_snippets_class_is_compact`) explicitly asserts that method bodies like `return 1` do not appear in the class snippet.

---

## 2. Embedding Generation & Storage

After snippets are extracted per file, `index_codebase()` does:

1. **Stores each snippet** in SQLite via `DatabaseManager.add_code_snippet()` → table `code_snippets`.
2. **Batch-embeds** the snippet text via `EmbeddingProvider.embed_batch()`.
   - Production: `OllamaEmbeddings` using the **nomic-embed-text** model (local, offline via Ollama).
   - Testing: `MockEmbeddings` producing deterministic vectors.
3. **Stores each embedding** in SQLite via `DatabaseManager.add_embedding()` → table `embeddings`, serialized as a JSON string with the vector dimensions recorded.

Metadata (provider name, model name) is saved to `indices/<codebase>_metadata.json` so query time can auto-detect the same model.

---

## 3. `retrieve_code` Query Flow & Similarity Algorithm

The MCP tool `retrieve_code()` in mcp_server.py:

1. Loads the codebase record from SQLite.
2. Reads `indices/<codebase>_metadata.json` to pick the **same** embedding provider/model used at index time.
3. Embeds the user's natural-language query → `query_vec`.
4. Calls `DatabaseManager.search_embeddings()` which:
   - Fetches **all** snippet+embedding rows for the codebase from SQLite.
   - Computes **cosine similarity** in pure Python:
     $$\text{similarity} = \frac{\vec{q} \cdot \vec{v}}{\|\vec{q}\| \cdot \|\vec{v}\|}$$
   - Filters by a threshold (default 0.0), sorts descending, returns top-k.
5. Returns the **full source code** of each matching snippet along with file path, line numbers, and similarity score.

---

## 4. Neo4j vs. SQLite — Complementary Roles

| Concern | SQLite (`DatabaseManager`) | Neo4j (`Neo4jGraphManager`) |
|---|---|---|
| **Purpose** | Vector store + snippet content | Structural knowledge graph |
| **Stores** | Codebases, source files, code snippets, embedding vectors (JSON blobs) | Nodes: `Codebase`, `CodeFile`, `Function`, `Class`; Relationships: `CONTAINS_FILE`, `CONTAINS_FUNCTION`, `CONTAINS_CLASS`, `CALLS`, `IMPORTS` |
| **Used by** | `retrieve_code` (semantic search) | `search_codebase` (keyword/structural search via Cypher) |
| **Query style** | Brute-force cosine similarity over all embeddings | Graph pattern matching — e.g. function dependency graphs, circular dependency detection, file→function→class traversals |

In short: **SQLite powers semantic "find by meaning"**, while **Neo4j powers structural "find by name/relationship"** (call graphs, import chains, class hierarchies). Both are populated during indexing but serve different MCP tools.