

The MCP tools aren't available, so I'll read the source files directly.     

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/embeddings.py)

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/mcp_server.py)

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/indexer.py#1-1), lines 1 to 200

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/database.py#1-1), lines 1 to 200

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/neo4j_graph.py#1-1), lines 1 to 200

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/database.py#200-200), lines 200 to 400

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/indexer.py#200-200), lines 200 to 400

Read [](file:///Users/glautenschlager/dev/project%20waverider/src/waverider/database.py#400-400), lines 400 to 467

Here's the full end-to-end picture:

---

## 1. Snippet Extraction

The indexer (indexer.py) uses Python's `ast` module to parse each `.py` file and extracts **five snippet types**:

| Type | What's captured |
|---|---|
| `function` | Full function body (via `ast.FunctionDef`) — every function and method, including those inside classes |
| `class` | **Compact summary** — class declaration + decorators + docstring + a one-liner list of method signatures (header only, no method bodies). This acts as a "table of contents" for the class. |
| `import` | `import` and `from … import` statements |
| `module_constant` | Top-level assignments (`Assign` / `AnnAssign` in `tree.body`) |
| `file` | Fallback — the entire file content, used only when AST parsing fails or when no other snippets are found |

**Class snippet structure** specifically ([indexer.py lines ~160–195](src/waverider/indexer.py#L160-L195)):
1. Header lines: decorators through the `class Foo(Base):` line
2. Docstring (if present)
3. Method signature summary: `# Methods:` followed by stripped first lines of each `FunctionDef` child (e.g. `def embed(self, text: str) -> List[float]:`)

Full method bodies are **not** included in the class snippet — they're indexed separately as `function` snippets.

For non-Python files, the whole file is returned as a single `file` snippet (AST parsing is Python-only today).

---

## 2. Embedding Generation & Storage

**Generation** (embeddings.py):
- Uses **Ollama** locally with the `nomic-embed-text` model (768-dimensional vectors).
- Each snippet's `content` string is passed to `ollama.embeddings()`.
- Batch embedding is sequential (calls `embed()` per text) — no server-side batching.

**Storage** (database.py):
- Embedding vectors are serialized as **JSON strings** and stored in SQLite's `embeddings` table alongside `snippet_id`, `model` name, and `embedding_dimensions`.
- The indexing loop in indexer.py processes files one at a time: extract snippets → insert into `code_snippets` → call `embed_batch()` → insert each vector into `embeddings`.

---

## 3. How `retrieve_code` Finds Snippets

At query time (mcp_server.py → database.py):

1. The query string is embedded with the same Ollama model used at index time (auto-detected from `indices/<name>_metadata.json`).
2. **All** embedding vectors for the codebase are loaded from SQLite into memory.
3. **Cosine similarity** is computed in pure Python for each stored vector against the query vector:

   $$\text{similarity} = \frac{\mathbf{q} \cdot \mathbf{v}}{\|\mathbf{q}\| \cdot \|\mathbf{v}\|}$$

4. Results are sorted descending by similarity, filtered by an optional `threshold` (default 0.0), and the top-`limit` are returned with full snippet content.

This is a brute-force linear scan — no approximate nearest-neighbor index.

---

## 4. Neo4j vs. SQLite — Division of Responsibility

| Concern | SQLite | Neo4j |
|---|---|---|
| **Purpose** | Vector store + snippet content | Structural knowledge graph |
| **What it stores** | Codebase metadata, source files, code snippets (full text), embedding vectors (JSON) | `Codebase`, `CodeFile`, `Function`, `Class` nodes and relationships (`CONTAINS_FILE`, `CONTAINS_FUNCTION`, `CONTAINS_CLASS`) |
| **Queried by** | `retrieve_code` (semantic/vector search) | `search_codebase` (keyword search via Cypher) |
| **Key data** | Full snippet bodies + embeddings | Names, signatures, docstrings, file paths, inheritance (`parent_class`) |
| **Search method** | Cosine similarity over all vectors | Cypher `CONTAINS` (case-insensitive substring match on names/paths) |

In short: **SQLite owns the vectors and raw code** for semantic retrieval, while **Neo4j owns the graph structure** for keyword-based discovery of functions, classes, and file-level relationships.