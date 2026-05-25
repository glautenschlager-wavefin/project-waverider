# Waverider

An **MCP (Model Context Protocol) server** for building vector indices and knowledge graphs over source code repositories. Waverider enables AI models to deeply understand and analyze codebases by creating searchable embeddings and relationship graphs.

## Features

- **Vector Indexing**: Extract code snippets (functions, classes, imports, module constants) and generate embeddings using Ollama (local)
- **ParadeDB/pgvector**: Postgres-based hybrid search with BM25 full-text (pg_bm25) and vector similarity (pgvector)
- **Neo4j Knowledge Graph**: Optional knowledge graph to model code structure, relationships, and dependencies
- **MCP Server Interface**: Expose indices through the Model Context Protocol for seamless AI integration
- **Multi-language Support**: Handles Python, JavaScript, TypeScript, Java, Go, Rust, and more
- **Incremental Indexing**: CocoIndex tracks file changes and only re-indexes what changed

## Quick Start

1. **Start infrastructure**:
   ```bash
   docker compose up -d
   ```

2. **Pull the embedding model** (if not already available):
   ```bash
   ollama pull nomic-embed-text
   ```

3. **Build an index**:
   ```bash
   make index
   ```

## Architecture

### Search flow

```
┌───────────────────────┐
│ AI Client / LLM Agent │
└───────────┬───────────┘
       │ MCP (SSE/stdout)
       ▼
┌─────────────────────────────────────────────────────┐
│ Waverider MCP Server                                │
│                                                     │
│  search(query, alpha)        explore_graph(entity)  │
│        │                            │               │
│        ├──── BM25 branch ───┐       │  Cypher query │
│        │  pg_bm25 keyword   │       │  (callers,    │
│        │  search w/ code-   │       │   callees,    │
│        │  aware tokenizer   │       │   methods,    │
│        │                    │       │   imports)    │
│        ├──── Vector branch ─┤       │               │
│        │  Embed query via   │       │               │
│        │  Ollama, pgvector  │       │               │
│        │  cosine similarity │       │               │
│        │                    │       │               │
│        ▼                    │       │               │
│  ┌────────────────────┐     │       │               │
│  │ Reciprocal Rank    │     │       │               │
│  │ Fusion (RRF, k=60) │◄────┘       │               │
│  │ weighted merge of  │             │               │
│  │ BM25 + vector ranks│             │               │
│  └────────┬───────────┘             │               │
│           ▼                         ▼               │
│    fused ranked results    graph relationships      │
└───────┬──────────────────────────┬──────────────────┘
        │ SQL queries              │ graph queries
        ▼                          ▼
┌──────────────────────────┐  ┌─────────────────────────┐
│ ParadeDB (Postgres 17)   │  │ Neo4j (service)         │
│ - coco_snippets table    │  │ - Codebase/File/Func/   │
│ - pgvector embeddings    │  │   Class nodes           │
│ - pg_bm25 full-text      │  │ - CALLS, IMPORTS,       │
│ - CocoIndex incremental  │  │   CONTAINS_* edges      │
└──────────┬───────────────┘  └─────────────────────────┘
           │
           │ embedding requests (index-time)
           ▼
┌─────────────────────────────────────────────┐
│ Ollama API (nomic-embed-text)               │
│ - host service on macOS/Windows Docker use  │
│   host.docker.internal                      │
│ - optional containerized Ollama (gpu)       │
└─────────────────────────────────────────────┘
```

### Index build flow

```
source code
     │
     ▼
┌──────────────────────────┐
│ CocoIndex pipeline       │  incremental — only re-indexes changed files
│ (Tree-sitter parsing)    │  extract functions, classes, imports, constants
└────────────┬─────────────┘
             │ snippets
             ├────────────────────────────────┐
             ▼                                ▼
  ┌─────────────────────┐        ┌─────────────────────────┐
  │ Ollama embeddings   │        │ Code-aware tokenizer    │
  │ (nomic-embed-text)  │        │ (camelCase/snake_case   │
  └──────────┬──────────┘        │  splitting for pg_bm25) │
             │                   └────────────┬────────────┘
             ▼                                ▼
  ┌─────────────────────┐        ┌─────────────────────────┐
  │ ParadeDB: pgvector  │        │ ParadeDB: pg_bm25 index │
  │ embeddings column   │        │ (BM25 keyword search)   │
  └─────────────────────┘        └─────────────────────────┘
             │
             ▼  (optional)
  ┌─────────────────────────┐
  │ Neo4j knowledge graph   │
  │ nodes + relationships   │
  └─────────────────────────┘
```

## Project Structure

See [SETUP.md](SETUP.md) for complete setup and development guide.

```
project waverider/
├── src/waverider/          # Main package
│   ├── database.py         # Postgres/ParadeDB queries
│   ├── indexer.py          # CocoIndex pipeline definition
│   ├── embeddings.py       # Embedding generation
│   ├── neo4j_graph.py      # Knowledge graph
│   └── mcp_server.py       # MCP interface
├── scripts/                # Command-line tools
│   ├── build_index.py      # Main indexing script
│   ├── setup_neo4j.py      # Initialize Neo4j
│   └── list_indices.py     # List built indices
├── docker-compose.yml      # ParadeDB + Neo4j + Waverider
├── Dockerfile              # Waverider container image
├── Makefile                # Convenience targets (index, index-repo, etc.)
├── indices/                # Index metadata (generated)
└── tests/                  # Test suite
```

## Technologies

- **Python 3.14+** - Core language
- **ParadeDB/pgvector** - Postgres-based hybrid search (BM25 + vector similarity)
- **CocoIndex** - Incremental indexing framework
- **Neo4j** - Optional knowledge graph database
- **Ollama** - Local embedding generation (nomic-embed-text)
- **Tree-sitter** - Multi-language code parsing

## Setup Guides

- **Quick Start**: See [SETUP.md - Quick Start](SETUP.md#quick-start-docker)
- **Database Configuration**: See [SETUP.md - Database Configuration](SETUP.md#database-configuration)
- **Neo4j Setup**: See [SETUP.md - Neo4j Configuration](SETUP.md#neo4j-configuration)
- **Index Building**: See [SETUP.md - Index Building](SETUP.md#index-building)
- **Troubleshooting**: See [SETUP.md - Troubleshooting](SETUP.md#troubleshooting)

## Usage Examples

### Index a codebase
```bash
# Index the waverider project itself
make index

# Index an external repo
make index-repo REPO=my-project REPO_PATH=/path/to/project
```

### List indices
```bash
python scripts/list_indices.py
```

### Get index statistics
```bash
python scripts/index_stats.py --index-name backend
```

### Check database status
```bash
make db-status
```

## Configuration

All configuration is via environment variables (see `docker-compose.yml` for defaults):

```
# Database (ParadeDB)
DATABASE_URL=postgresql://waverider:changeme@localhost:5432/waverider

# Ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=nomic-embed-text

# Neo4j (optional)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme
```

> **Tip:** On macOS, Ollama runs natively for Metal GPU acceleration. Docker containers reach it via `host.docker.internal:11434` (configured automatically in `docker-compose.yml`).

## Neo4j Runtime

Both ParadeDB and Neo4j run as Docker Compose services — `docker compose up -d` starts everything:

```bash
docker compose up -d   # starts paradedb + neo4j + waverider
docker compose ps      # check service health
```

Then initialize and verify Waverider's Neo4j integration:

```bash
poetry run python scripts/setup_neo4j.py
poetry run python scripts/test_neo4j_connection.py
```

<details>
<summary>Advanced: native Neo4j (without Docker)</summary>

```bash
brew install neo4j
brew services start neo4j
# Browser: http://localhost:7474 | Bolt: bolt://localhost:7687
```
</details>

## Using Waverider with AI Agents

When this project is opened in an AI-enabled code editor (like VS Code with GitHub Copilot), Waverider exposes two MCP tools for semantic code search:

### Available Tools

1. **`search_codebase(query, codebase_name, limit, alpha)`** — Hybrid search via ParadeDB (BM25 + pgvector)
   - Best for: Finding code by name, keyword, concept, or behavior
   - `alpha` controls BM25 vs vector weighting (0.0 = keyword only, 1.0 = semantic only)
   - Example: "Find DatabaseManager implementation"

2. **`explore_graph(entity_name, codebase_name, relationship)`** — Structural traversal via Neo4j
   - Best for: Call graphs, method lists, import relationships
   - Example: "What calls `search_embeddings`?"

### Configuration

The MCP server is defined in `.vscode/mcp.json` and launched automatically when the workspace is opened. For more details, see [AGENTS.md](AGENTS.md).

### Example Agent Query

In a chat with the AI agent in this workspace:

> "How do Waverider's ParadeDB and Neo4j layers work together to index code?"

The agent will automatically use the MCP tools to:
1. Search for database-related code (`search_codebase`)
2. Explore structural relationships (`explore_graph`)
3. Summarize relationships and answer your question

See [AGENTS.md](AGENTS.md) for a complete agent decision tree and troubleshooting guide.

## Contributing

This is an experimental project. Contributions welcome!

- Report issues
- Submit PRs with new features
- Improve documentation
- Add support for more languages

## License

MIT