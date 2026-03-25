# Copilot Instructions for Waverider Project

## MCP Tools Available

This workspace has access to **Waverider** — a semantic code search and indexing system via MCP (Model Context Protocol).

### Available Tools

1. **`search_codebase`** (keyword search via Neo4j)
   - Use for: Finding code by name, class, function, keyword
   - Parameters: `query`, `codebase_name`, `limit`
   - Example: "Find all uses of DatabaseManager"

2. **`retrieve_code`** (semantic search via embeddings)
   - Use for: Finding code by concept, functionality, design pattern
   - Parameters: `query`, `codebase_name`, `limit`
   - Example: "Find code that handles database connection pooling"

### When to Use Waverider

- User asks questions about **how the code works** or **how things are implemented**
- User wants to **understand relationships** between classes/functions
- User asks about **code patterns** or **architecture**
- User requests **code examples** from the codebase
- User wants to **find specific implementations** of concepts

### When NOT to Use Waverider

- Simple questions about the project (README is often faster)
- User is asking about setup/installation
- User is asking theoretical questions unrelated to the actual codebase

### Default Codebase

The codebase name is `waverider` — use this when calling the MCP tools.

## Waverider System Overview

Waverider indexes Python codebases using:
- **Embeddings**: OpenAI, Ollama (local), or Mock providers
- **Storage**: SQLite (embeddings) + Neo4j (relationships)
- **Search**: Semantic (embedding similarity) + Keyword (Neo4j cypher)
- **Extraction**: AST-based Python snippets (functions, classes, methods)

Use the MCP tools to let users explore their codebase semantically.
