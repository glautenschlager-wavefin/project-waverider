# Waverider

An **MCP (Model Context Protocol) server** for building vector indices and knowledge graphs over source code repositories. Waverider enables AI models to deeply understand and analyze codebases by creating searchable embeddings and relationship graphs.

## Features

- **Vector Indexing**: Extract code snippets (functions, classes, imports) and generate embeddings using OpenAI or other providers
- **SQLite Storage**: Lightweight, file-based database for storing code metadata and embeddings
- **Neo4j Knowledge Graph**: Optional knowledge graph to model code structure, relationships, and dependencies
- **MCP Server Interface**: Expose indices through the Model Context Protocol for seamless AI integration
- **Multi-language Support**: Handles Python, JavaScript, TypeScript, Java, Go, Rust, and more
- **Dependency Analysis**: Understand function calls, imports, and circular dependencies

## Quick Start

1. **Setup the environment**:
   ```bash
   pip install poetry
   poetry env use python3.14
   poetry install
   poetry shell
   ```

2. **Initialize SQLite**:
   ```bash
   python scripts/setup_sqlite.py
   ```

3. **Build an index**:
   ```bash
   # Using OpenAI embeddings (requires OPENAI_API_KEY)
   python scripts/build_index.py --codebase-path /path/to/code --index-name my-project

   # Or using mock embeddings for testing
   python scripts/build_index.py --codebase-path /path/to/code --index-name my-project --embedding-provider mock
   ```

## Architecture

```
┌─────────────────────────────────────────┐
│         Source Code Codebase            │
└──────────────────┬──────────────────────┘
                   │
        ┌──────────┴──────────┬──────────────┐
        │                     │              │
    ┌───▼────┐         ┌─────▼────┐    ┌───▼──────┐
    │CodeFile│         │ Embeddings│    │Snippet   │
    └──┬─────┘         └──────┬────┘    │Metadata  │
       │                      │         └──────────┘
       │    ┌─────────────────┘
       │    │
    ┌──▼────▼──────────┐          ┌─────────────┐
    │   SQLite DB      │          │  Neo4j      │
    │   (Embeddings    │          │  (Knowledge │
    │   + Metadata)    │          │   Graph)    │
    └─────┬────────────┘          └────────────┐
          │                                    │
          └────────────────┬─────────────────┘
                           │
                    ┌──────▼──────┐
                    │ MCP Server  │
                    │(Query API)  │
                    └──────┬──────┘
                           │
                      ┌────▼────┐
                      │   LLM   │
                      └─────────┘
```

## Project Structure

See [SETUP.md](SETUP.md) for complete setup and development guide.

```
project waverider/
├── src/waverider/          # Main package
│   ├── database.py         # SQLite management
│   ├── indexer.py          # Code extraction & indexing
│   ├── embeddings.py       # Embedding generation
│   ├── neo4j_graph.py      # Knowledge graph
│   └── mcp_server.py       # MCP interface
├── scripts/                # Command-line tools
│   ├── build_index.py      # Main indexing script
│   ├── setup_sqlite.py     # Initialize SQLite
│   ├── setup_neo4j.py      # Initialize Neo4j
│   └── list_indices.py     # List built indices
├── data/                   # Data directory (generated)
├── indices/                # Index metadata (generated)
└── tests/                  # Test suite
```

## Technologies

- **Python 3.14+** - Core language
- **SQLite** - File-based vector database
- **Neo4j** - Optional knowledge graph database
- **OpenAI API** - Embedding generation (configurable)
- **AST Parsing** - Code structure analysis

## Setup Guides

- **SQLite Setup**: See [SETUP.md - SQLite Configuration](SETUP.md#sqlite-configuration)
- **Neo4j Setup**: See [SETUP.md - Neo4j Configuration](SETUP.md#neo4j-configuration)
- **Index Building**: See [SETUP.md - Index Building](SETUP.md#index-building)
- **Development**: See [SETUP.md - Development](SETUP.md#development)

## Usage Examples

### Index a codebase
```bash
python scripts/build_index.py \
  --codebase-path /path/to/project \
  --index-name backend \
  --description "Backend API codebase"
```

### List indices
```bash
python scripts/list_indices.py
```

### Get index statistics
```bash
python scripts/index_stats.py --index-name backend
```

### Query an index (programmatic)
```python
from waverider.database import DatabaseManager

db = DatabaseManager()
codebase = db.get_codebase("backend")
stats = db.get_statistics(codebase["id"])
print(f"Total snippets: {stats['total_snippets']}")
```

## Configuration

Create a `.env` file in the project root:

```
# OpenAI API
OPENAI_API_KEY=sk-...

# Neo4j (if using)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

## Neo4j Runtime

SQLite is embedded and needs no separate process. Neo4j is different: it must be running as its own database service before Waverider can connect to it.

For local macOS development, Homebrew is the simplest option:

```bash
brew install neo4j
brew services start neo4j
```

Useful management commands:

```bash
# Start Neo4j as a background service
brew services start neo4j

# Stop the service
brew services stop neo4j

# Restart after config changes
brew services restart neo4j

# Check service status
brew services list | grep neo4j

# Run in the foreground instead of as a service
/opt/homebrew/opt/neo4j/bin/neo4j console
```

Once running, the local endpoints are typically:

```text
Browser: http://localhost:7474
Bolt:    bolt://localhost:7687
```

Then initialize and verify Waverider's Neo4j integration:

```bash
poetry run python scripts/setup_neo4j.py
poetry run python scripts/test_neo4j_connection.py
```

## Contributing

This is an experimental project. Contributions welcome!

- Report issues
- Submit PRs with new features
- Improve documentation
- Add support for more languages

## License

MIT