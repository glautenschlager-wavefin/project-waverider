# Waverider Setup Guide

This guide covers setup and development environment configuration for the Waverider project.

## Quick Start

### Prerequisites
- Python 3.14+
- Poetry (install via `pip install poetry`)
- Optional: Docker (for Neo4j)

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

# Activate the virtual environment
poetry shell
```

Alternatively, use `poetry run` to run commands without activating the shell:
```bash
poetry run python scripts/setup_sqlite.py
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

## SQLite Configuration

### Setting Up SQLite

SQLite is a lightweight, file-based database that requires no server setup.

```bash
# SQLite is built into Python, but you may want to install the sqlite3 CLI
# macOS (Homebrew)
brew install sqlite

# Ubuntu/Linux
sudo apt-get install sqlite3
```

### Database Structure

Create the main database file:

```bash
python scripts/setup_sqlite.py
```

This script will:
1. Create `data/waverider.db` (SQLite database file)
2. Initialize core tables:
   - `codebase_metadata` - Tracks codebase information
   - `source_files` - File metadata and checksums
   - `code_snippets` - Extracted code segments
   - `embeddings` - Vector embeddings and metadata
   - `indices` - Index build history and status

### SQLite Usage Example

```python
import sqlite3

conn = sqlite3.connect('data/waverider.db')
cursor = conn.cursor()

# Query example
cursor.execute('SELECT * FROM codebase_metadata')
results = cursor.fetchall()
conn.close()
```

### Database Backup

```bash
# Backup the SQLite database
cp data/waverider.db data/waverider.db.backup

# In Python
python scripts/backup_sqlite.py
```

## Neo4j Configuration

### Option 1: Docker Setup (Recommended)

Neo4j requires a running server instance. Using Docker is the simplest approach:

```bash
# Pull the Neo4j docker image
docker pull neo4j:latest

# Run Neo4j in Docker
docker run \
  --name waverider-neo4j \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password_here \
  -v neo4j_data:/data \
  neo4j:latest
```

### Option 2: Local Installation

**macOS (Homebrew):**
```bash
brew install neo4j
brew services start neo4j
```

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
python scripts/test_neo4j_connection.py
```

Access Neo4j Browser at: `http://localhost:7474/browser/`

## Index Building

### Building Vector Indices

Waverider can build vector indices over source code using embeddings:

```bash
# Full index build (SQLite + Neo4j)
python scripts/build_index.py \
  --codebase-path /path/to/codebase \
  --index-name my_codebase \
  --use-sqlite \
  --use-neo4j

# SQLite only
python scripts/build_index.py \
  --codebase-path /path/to/codebase \
  --index-name my_codebase \
  --use-sqlite

# Neo4j only
python scripts/build_index.py \
  --codebase-path /path/to/codebase \
  --index-name my_codebase \
  --use-neo4j
```

### Script Options

- `--codebase-path`: Path to the source code directory
- `--index-name`: Unique identifier for this index
- `--use-sqlite`: Build SQLite-backed indices
- `--use-neo4j`: Build Neo4j knowledge graph
- `--model`: Embedding model (default: "text-embedding-3-small")
- `--exclude-patterns`: Patterns to exclude (e.g., ".git", "__pycache__")
- `--chunk-size`: Code snippet chunk size (default: 1024)
- `--batch-size`: Processing batch size (default: 10)

### Checking Index Status

```bash
# List all built indices
python scripts/list_indices.py

# Get details about a specific index
python scripts/index_stats.py --index-name my_codebase
```

## Project Structure

```
project waverider/
├── README.md                 # Project overview
├── SETUP.md                  # This file
├── pyproject.toml           # Python project metadata
├── requirements.txt         # Python dependencies
├── .gitignore              # Git ignore rules
│
├── src/
│   └── waverider/
│       ├── __init__.py
│       ├── mcp_server.py   # MCP server implementation
│       ├── database.py     # Database utilities
│       ├── embeddings.py   # Embedding generation
│       ├── indexer.py      # Index building logic
│       └── neo4j_graph.py  # Neo4j integration
│
├── scripts/
│   ├── build_index.py      # Main index builder
│   ├── setup_sqlite.py     # SQLite initialization
│   ├── setup_neo4j.py      # Neo4j schema setup
│   ├── list_indices.py     # List built indices
│   ├── index_stats.py      # Index statistics
│   └── test_neo4j_connection.py
│
├── data/
│   └── waverider.db        # SQLite database (generated)
│
├── indices/                 # Index data (generated)
│
└── tests/
    ├── __init__.py
    ├── test_database.py
    ├── test_indexer.py
    └── test_mcp_server.py
```

## Environment Variables

Create a `.env` file in the project root:

```
# OpenAI API
OPENAI_API_KEY=your_key_here

# Neo4j Connection
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# Waverider Config
WAVERIDER_DATA_DIR=./data
WAVERIDER_INDICES_DIR=./indices
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

### SQLite Issues

**"database is locked"**
- Close other connections to the database
- Use `sqlite3` with `timeout` parameter

### Neo4j Issues

**"Connection refused"**
- Verify Neo4j is running: `docker ps` or `systemctl status neo4j`
- Check URI and credentials in `.env` or config

**"OutOfMemory errors"**
- Increase Docker memory: `docker update -m 4g waverider-neo4j`
- Or in Neo4j config: `dbms.memory.heap.max_size=2g`

### Embeddings Issues

**"OpenAI API rate limited"**
- Implement request throttling
- Use smaller batch sizes
- Cache embeddings in SQLite

## Next Steps

1. Create your first index over a test codebase
2. Query the SQLite database for code snippets
3. Explore the Neo4j knowledge graph
4. Build the MCP server interface

See the main [README.md](README.md) for architecture details.
