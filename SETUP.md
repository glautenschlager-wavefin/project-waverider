# Waverider Setup Guide

This guide covers setup and development environment configuration for the Waverider project.

## Quick Start

### Prerequisites
- Python 3.14+
- Poetry (install via `pip install poetry`)
- Docker & Docker Compose (required for ParadeDB + Neo4j)
- Ollama (local embedding generation — install via `brew install ollama` on macOS)

### Initial Setup

```bash
# Navigate to project directory
cd project\ waverider

# Install Poetry (if not already installed)
pip install poetry

# Use the project Python version explicitly
poetry env use python3.14

# Install dependencies
poetry install

# Start infrastructure (ParadeDB + Neo4j)
docker compose up -d

# Pull the embedding model
ollama pull nomic-embed-text

# Index the waverider codebase itself
make index
```

### Setup Wizard (Recommended)

For first-time setup on a development machine, use the guided wizard:

```bash
make setup
```

The wizard performs these steps:

1. Installs/starts Ollama and pulls `nomic-embed-text`
2. Builds the Waverider image and starts ParadeDB + Neo4j
3. Registers Waverider MCP in VS Code
4. Lets you select local Wave repos to index
5. Runs initial indexing for selected repos
6. Prompts to install an automatic reindex cron job

If enabled, the cron job runs:

```bash
docker compose run --rm --entrypoint python waverider scripts/reindex_if_changed.py --once
```

Cron defaults:

- Schedule: `*/30 * * * *`
- Log file: `/tmp/waverider-reindex-cron.log`
- Tag: `WAVERIDER_REINDEX_UPDATES`

You can install or update it manually at any time:

```bash
make cron-setup-index-updates
make cron-setup-index-updates CRON_SCHEDULE="0 * * * *" CRON_LOG="/tmp/waverider-hourly.log"
```

When uninstalling Waverider (`make uninstall`), cron entries tagged `WAVERIDER_REINDEX_UPDATES` are removed automatically.

Alternatively, use `poetry run` to run commands without activating the shell:
```bash
poetry run python -m waverider.mcp_server
```

If Poetry already created an environment with an older Python version, recreate it:
```bash
poetry env remove --all
poetry env use python3.14
poetry install
```

### Optional: Setup Pre-commit Hooks

To automatically format and lint code before commits:

```bash
# Install pre-commit
poetry add --group dev pre-commit

# Setup hooks
pre-commit install

# (Optional) Run on all files
pre-commit run --all-files
```

Now your code will be automatically formatted and checked on each commit!

## Database Configuration (ParadeDB)

Waverider uses **ParadeDB** — a Postgres distribution bundled with pgvector (vector similarity) and pg_bm25 (full-text search). All data lives in a single Postgres cluster.

### Starting the Database

```bash
# Start all services (ParadeDB, Neo4j, MCP server)
docker compose up -d

# Verify ParadeDB is healthy
docker compose exec paradedb pg_isready -U waverider -d waverider
```

### Database Schema

CocoIndex manages the schema automatically on first index run. The primary table is:

- **`coco_snippets`** — Code snippets with embeddings, BM25 index, and metadata

### Connecting to the Database

```bash
# Open a psql shell
make db-shell

# Check table sizes and row counts
make db-status
```

### Programmatic Access

```python
from waverider.database import DatabaseManager

db = DatabaseManager()
stats = db.get_statistics()
print(f"CocoIndex rows: {stats['coco_row_count']}")

# Search via embeddings
results = db.search_coco_embeddings(query_vector, limit=10)

# Search via BM25 keywords
results = db.search_coco_bm25("DatabaseManager", limit=10)
```

### Database Backup

```bash
# Dump the entire database
docker compose exec paradedb pg_dump -U waverider waverider > backup.sql

# Restore from backup
cat backup.sql | docker compose exec -T paradedb psql -U waverider -d waverider
```

## Neo4j Configuration

Neo4j provides optional knowledge-graph features (call graphs, dependency analysis). It runs as a service alongside ParadeDB in Docker Compose.

### Docker Setup (default)

Neo4j starts automatically with `docker compose up -d`. Default credentials:
- **Bolt**: `bolt://localhost:7687`
- **Browser**: `http://localhost:7474`
- **User/Password**: `neo4j` / `changeme` (override with `NEO4J_PASSWORD` env var)

### Option 2: Local Installation (alternative)

For local development without Docker, you can install Neo4j directly:

**macOS (Homebrew):**

Service management on macOS:

```bash
# Start in the background and restart on login
brew services start neo4j

# Stop the background service
brew services stop neo4j

# Restart after config changes
brew services restart neo4j

# Confirm whether the service is running
brew services list | grep neo4j

# Run in the foreground for debugging
/opt/homebrew/opt/neo4j/bin/neo4j console
```

Homebrew currently installs Neo4j with `openjdk@21` and `cypher-shell` as dependencies, so `brew install neo4j` is sufficient for a normal local setup.

**Ubuntu/Linux:**
```bash
wget -O - https://debian.neo4j.com/neotechnology.asc | sudo apt-key add -
echo 'deb https://debian.neo4j.com stable latest' | sudo tee /etc/apt/sources.list.d/neo4j.list
sudo apt-get update
sudo apt-get install neo4j
sudo systemctl start neo4j
```

### Configuration

Create a `.env` file (or update `waverider/config.py`):

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password_here
NEO4J_PROTOCOL=bolt
```

### Initialization

```bash
# Initialize Neo4j schema and constraints
python scripts/setup_neo4j.py
```

This creates:
- **Nodes**: CodeFile, Function, Class, Variable, Module, Codebase
- **Relationships**: CONTAINS, CALLS, INHERITS, DEPENDS_ON, IMPORTS
- **Constraints**: Unique indices on key properties

### Verify Neo4j Connection

```bash
poetry run python scripts/test_neo4j_connection.py
```

Access Neo4j Browser at: `http://localhost:7474/browser/`

## Index Building

### Building Vector Indices

Waverider uses **CocoIndex** for incremental indexing. CocoIndex tracks file changes and only re-indexes what changed.

```bash
# Index the waverider codebase itself (runs in Docker)
make index

# Index a Wave service repo
make index-repo REPO=accounting

# Index all configured Wave repos
make index-all
```

### Running the indexer directly

```bash
python scripts/build_index.py \
  --codebase-path /path/to/codebase \
  --index-name my_codebase \
  --description "My project's source code"
```

### Script Options

- `--codebase-path`: Path to the source code directory
- `--index-name`: Unique identifier for this index
- `--description`: Human-readable description of the codebase

### Checking Index Status

```bash
# List all built indices
python scripts/list_indices.py

# Get details about a specific index
python scripts/index_stats.py --index-name my_codebase

# Check database table sizes
make db-status
```

## Project Structure

```
project waverider/
├── README.md                 # Project overview
├── SETUP.md                  # This file
├── AGENTS.md                 # Agent decision tree & MCP tool docs
├── docker-compose.yml        # ParadeDB + Neo4j + Waverider services
├── Makefile                  # Convenience commands
├── pyproject.toml            # Python project metadata
├── requirements.txt          # Python dependencies
│
├── src/
│   └── waverider/
│       ├── __init__.py
│       ├── mcp_server.py     # MCP server (search + explore_graph)
│       ├── database.py       # ParadeDB/Postgres queries
│       ├── embeddings.py     # Embedding generation (Ollama)
│       ├── fusion.py         # Reciprocal Rank Fusion
│       ├── indexer.py        # Code extraction (tree-sitter)
│       ├── neo4j_graph.py    # Neo4j knowledge graph
│       └── treesitter_parser.py  # Multi-language AST parsing
│
├── scripts/
│   ├── build_index.py        # Main indexing script (CocoIndex)
│   ├── setup_neo4j.py        # Neo4j schema setup
│   ├── validate_corpus.py    # Post-index validation
│   ├── load_test.py          # Search performance benchmarks
│   ├── list_indices.py       # List built indices
│   ├── index_stats.py        # Index statistics
│   ├── discover_repos.py     # Discover org repos into the registry
│   ├── seed_default_repos.py # Enable common Wave services
│   └── reindex_if_changed.py # Sync managed clones & reindex on change
│
├── indices/                  # Index metadata (generated)
│
└── tests/
    ├── __init__.py
    ├── test_database.py
    ├── test_fusion.py
    └── test_indexer.py
```

## Environment Variables

Create a `.env` file in the project root:

```
# Database (ParadeDB)
DATABASE_URL=postgresql://waverider:changeme@localhost:5432/waverider
COCOINDEX_DB_URL=postgresql://waverider:changeme@localhost:5432/waverider
POSTGRES_PASSWORD=changeme

# Ollama (embedding generation)
OLLAMA_URL=http://localhost:11434
OLLAMA_HOST=http://host.docker.internal:11434   # Used by Docker containers
OLLAMA_MODEL=nomic-embed-text

# Neo4j (optional knowledge graph)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme

# Waverider
WAVERIDER_SEARCH_BACKEND=postgres   # or "neo4j"
LOG_LEVEL=INFO
```

## Development

### Running Tests

```bash
poetry run pytest tests/
poetry run pytest tests/test_database.py -v
poetry run pytest tests/ --cov=src/waverider
```

### Code Formatting and Linting

This project uses a standard Python code quality stack:

**Black** - Code formatter (enforces consistent style)
```bash
poetry run black src/ scripts/ tests/
```

**isort** - Import sorting (organizes imports by type and alphabetically)
```bash
poetry run isort src/ scripts/ tests/
```

**Ruff** - Fast Python linter (checks for common errors and style issues)
```bash
poetry run ruff check src/ scripts/ tests/
poetry run ruff check --fix src/ scripts/ tests/  # Auto-fix issues
```

**mypy** - Static type checker (validates type hints)
```bash
poetry run mypy src/
```

**Run all code quality checks:**
```bash
poetry run black --check src/ scripts/ tests/
poetry run isort --check-only src/ scripts/ tests/
poetry run ruff check src/ scripts/ tests/
poetry run mypy src/
```

**Auto-format code:**
```bash
poetry run black src/ scripts/ tests/
poetry run isort src/ scripts/ tests/
poetry run ruff check --fix src/ scripts/ tests/
```

### Convenience Commands with Make

We provide a Makefile for common development tasks:

```bash
make help          # Show all available commands
make install       # Install dependencies
make format        # Format code (black + isort)
make lint          # Run linters (ruff)
make lint-fix      # Auto-fix linter issues
make type-check    # Run type checker (mypy)
make test          # Run pytest
make all-checks    # Run all checks
make clean         # Remove cache files
```

Example workflow:
```bash
make install       # One-time setup
make all-checks    # Verify all before committing
make format        # Auto-fix formatting issues
```

### Managing Dependencies

Add a new dependency:
```bash
poetry add package_name
poetry add --group dev package_name  # Dev-only dependency
```

Update dependencies:
```bash
poetry update
```

Export requirements (if needed):
```bash
poetry export --format=requirements.txt --output=requirements-lock.txt
```

### MCP Server Development

The MCP server connects Waverider indices to LLMs:

```bash
# Start the MCP server
poetry run python -m waverider.mcp_server

# Or in development mode with hot-reload
poetry run python scripts/run_dev_server.py
```

## Troubleshooting

### Ollama + Docker Issues

**"Docker containers cannot reach Ollama"**
- Cause: Ollama is bound to `127.0.0.1` only.
- Fix on macOS (Homebrew service):

```bash
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
brew services restart ollama
```

- Verify from host:

```bash
curl http://localhost:11434/api/version
```

- Verify from a container:

```bash
docker run --rm curlimages/curl:8.7.1 curl -s http://host.docker.internal:11434/api/version
```

- Note: Docker Desktop on macOS/Windows resolves `host.docker.internal` automatically.
  On native Linux Docker, add a host mapping such as
  `extra_hosts: ["host.docker.internal:host-gateway"]` if resolution fails.

### ParadeDB Issues

**"Connection refused" on port 5432**
- Verify ParadeDB is running: `docker compose ps`
- Check logs: `docker compose logs paradedb`
- Ensure port 5432 is not used by another Postgres instance

**"relation coco_snippets does not exist"**
- Run `make index` to trigger CocoIndex schema creation
- Verify connection: `make db-shell` then `\dt`

### Neo4j Issues

**"Connection refused"**
- Verify Neo4j is running: `docker ps` or `systemctl status neo4j`
- Check URI and credentials in `.env` or config

**"OutOfMemory errors"**
- Increase Docker memory: `docker update -m 4g waverider-neo4j`
- Or in Neo4j config: `dbms.memory.heap.max_size=2g`

### Embeddings Issues

**"Ollama model not found"**
- Pull the model: `ollama pull nomic-embed-text`
- Verify: `ollama list`

## Next Steps

1. Start infrastructure: `docker compose up -d`
2. Index a codebase: `make index` or `make index-repo REPO=<name>`
3. Explore the Neo4j knowledge graph at `http://localhost:7474`
4. Open the workspace in VS Code — MCP tools are available in Copilot Chat

See the main [README.md](README.md) for architecture details and [AGENTS.md](AGENTS.md) for agent integration.
