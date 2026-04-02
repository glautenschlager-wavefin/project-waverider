# Copilot Instructions for Waverider Project

## MCP Tools Available

This workspace has access to **Waverider** — a semantic code search and indexing system via MCP (Model Context Protocol).

### Available Tools

1. **`search_codebase`** (keyword search via Neo4j)
   - Use for: Finding code by name, class, function, keyword
   - Returns: File paths, matching function signatures with docstrings, class names with docstrings, and a full list of all functions/classes in each file
   - Parameters: `query`, `codebase_name`, `limit`

2. **`retrieve_code`** (semantic search via embeddings)
   - Use for: Finding code by concept, functionality, design pattern
   - Returns: **Full source code** of matching functions/methods with docstrings, line numbers, and similarity scores
   - Parameters: `query`, `codebase_name`, `limit`

### How to Use Waverider Results

**IMPORTANT: When Waverider MCP tools return relevant code snippets, use them directly to answer questions. Do not read original source files unless the snippets are clearly insufficient for the question at hand.**

### Default Policy (Waverider-First)

- For implementation, execution flow, architecture, relationship, and "where is X called" questions, call Waverider first.
- Do not default to local grep/read_file just because the repo is open or files are accessible.
- A direct local-file-first approach is only acceptable when the user explicitly asks for a specific file edit, a tiny single-file clarification, or setup/docs metadata where code search is unnecessary.
- If Waverider returns incomplete context, then read only the minimal missing local files.

- `retrieve_code` returns complete function/method implementations. Prefer it for "how does X work?" questions.
- `search_codebase` returns structural overviews with signatures and docstrings. Use it to discover what exists, then use `retrieve_code` for implementation details.
- Only fall back to reading source files if the returned snippets don't cover the specific context needed (e.g., module-level configuration, cross-file relationships not captured in snippets).

### When to Use Waverider

- User asks questions about **how the code works** or **how things are implemented**
- User wants to **understand relationships** between classes/functions
- User asks about **code patterns** or **architecture**
- User requests **code examples** from the codebase
- User wants to **find specific implementations** of concepts
- User asks to trace a request path, caller/callee chain, or end-to-end flow

### When NOT to Use Waverider

- Simple questions about the project (README is often faster)
- User is asking about setup/installation
- User is asking theoretical questions unrelated to the actual codebase

### Anti-Pattern to Avoid

- "I skipped Waverider because the repo is local/open" is not a valid reason for implementation analysis in this workspace.

### Default Codebase

The codebase name is `waverider` — use this when calling the MCP tools.

## Waverider System Overview

Waverider indexes Python codebases using:
- **Embeddings**: Ollama (local, offline: nomic-embed-text)
- **Storage**: SQLite (embeddings + snippets) + Neo4j (structural relationships)
- **Search**: Semantic (embedding similarity) + Keyword (Neo4j cypher)
- **Extraction**: AST-based Python snippets (functions, classes with method signatures, module constants, imports)

Use the MCP tools to let users explore their codebase semantically.
