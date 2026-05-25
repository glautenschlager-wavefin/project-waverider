# Waverider Backend Configuration Guide

## Overview

Waverider now supports configurable search backends to enable side-by-side validation during the migration from Neo4j to Postgres.

## Supported Backends

### Postgres (Default)
- **Symbol Search**: Fast lookups of files, functions, classes via `search_codebase()`
- **Semantic Search**: Vector embeddings via pgvector + HNSW indexing
- **Keyword Search**: BM25 via pg_bm25 extension (with tsvector fallback)
- **Hybrid**: RRF fusion of BM25 + vector results for `retrieve_code()`

### Neo4j
- **Symbol Search**: Graph traversal via Cypher queries
- **Keyword Search**: Not supported
- **Semantic Search**: Not supported (use Postgres for `retrieve_code()`)

## Environment Variables

### WAVERIDER_SEARCH_BACKEND
Switch between search backends.

```bash
# Use Postgres (default)
export WAVERIDER_SEARCH_BACKEND=postgres

# Use Neo4j
export WAVERIDER_SEARCH_BACKEND=neo4j
```

### WAVERIDER_SEARCH_HYBRID
Control hybrid search (vector + keyword fusion) in `retrieve_code()`.

```bash
# Enable hybrid search (default) - RRF fusion of BM25 + embeddings
export WAVERIDER_SEARCH_HYBRID=true

# Vector-only search (disables BM25 component)
export WAVERIDER_SEARCH_HYBRID=false
```

### WAVERIDER_FALLBACK_ENABLED
Allow fallback to secondary backend if primary search fails.

```bash
# Enable fallback (default)
export WAVERIDER_FALLBACK_ENABLED=true

# Disable fallback - fail fast if primary backend unavailable
export WAVERIDER_FALLBACK_ENABLED=false
```

## Usage Examples

### Example 1: Default Postgres Mode
```bash
# No environment variables needed
poetry run python -m waverider.mcp_server

# search_codebase() → Postgres symbol search (fast, prioritizes exact name matches)
# retrieve_code() → Hybrid search (RRF fusion of BM25 + vector embeddings)
```

### Example 2: Neo4j Symbol Search Only
```bash
export WAVERIDER_SEARCH_BACKEND=neo4j
export WAVERIDER_FALLBACK_ENABLED=false
poetry run python -m waverider.mcp_server

# search_codebase() → Neo4j graph traversal
# retrieve_code() → ERROR (semantic search requires Postgres)
```

### Example 3: Vector-Only Search (No BM25)
```bash
export WAVERIDER_SEARCH_HYBRID=false
poetry run python -m waverider.mcp_server

# search_codebase() → Postgres symbol search
# retrieve_code() → Vector-only (no keyword fusion)
```

### Example 4: Validation Mode (Postgres with Neo4j Fallback)
```bash
export WAVERIDER_SEARCH_BACKEND=postgres
export WAVERIDER_FALLBACK_ENABLED=true
poetry run python -m waverider.mcp_server

# search_codebase() → Tries Postgres first, falls back to Neo4j if no results
# Useful for validating Postgres coverage against Neo4j
```

## MCP Tools

### search_codebase(query, codebase_name, limit)
Searches using the configured backend.

- **Postgres**: Symbol search (files, functions, classes) with priority ranking
- **Neo4j**: Graph traversal via Cypher
- **Fallback**: Tries alternative backend if enabled and primary returns no results

```json
{
  "tool": "search_codebase",
  "params": {
    "query": "DatabaseManager",
    "codebase_name": "waverider",
    "limit": 10
  }
}
```

### retrieve_code(query, codebase_name, limit)
Semantic search using vector embeddings (Postgres only).

- **Hybrid Mode (default)**: RRF fusion of BM25 + vector results
- **Vector Mode**: Vector embeddings only (when WAVERIDER_SEARCH_HYBRID=false)

```json
{
  "tool": "retrieve_code",
  "params": {
    "query": "how are embeddings generated",
    "codebase_name": "waverider",
    "limit": 5
  }
}
```

### get_config()
Returns current configuration settings (useful for debugging).

```json
{
  "tool": "get_config"
}
```

Example output:
```
Waverider Configuration:
  Backend: postgres
  Hybrid Search: enabled (vector + keyword)
  Fallback Enabled: true

Environment Variables:
  WAVERIDER_SEARCH_BACKEND=not set (default: postgres)
  WAVERIDER_SEARCH_HYBRID=not set (default: true)
  WAVERIDER_FALLBACK_ENABLED=not set (default: true)
```

## Backend Comparison

| Feature | Postgres | Neo4j |
|---------|----------|-------|
| Symbol search | ✅ Fast (Postgres index) | ✅ Graph traversal |
| Exact name matching | ✅ Prioritized | ❌ No priority |
| Keyword search (BM25) | ✅ pg_bm25 + fallback | ❌ Not supported |
| Vector search | ✅ pgvector HNSW | ❌ Not supported |
| Hybrid search (RRF) | ✅ Yes | ❌ No |
| Graph relationships | ❌ Limited | ✅ Full call graphs |
| Scalability | ✅ Excellent | ⚠️ Graph-limited |

## Migration Path

### Phase 3: Search Cutover (Current)
1. **Step 10** ✅ Postgres symbol search implemented and tested
2. **Step 11** ✅ BM25 behavior preserved via RRF fusion
3. **Step 12** ✅ Backend configuration system added
4. **Phase 4** (next) Operational cutover to Postgres

### Phase 4: Operational Cutover
- Index all 7 codebases with `build_index.py`
- Validate Postgres search results match Neo4j
- Switch to postgres backend (WAVERIDER_SEARCH_BACKEND=postgres)
- Monitor performance and coverage

## Troubleshooting

### Problem: retrieve_code() fails with "semantic search requires Postgres"
**Solution**: Set `WAVERIDER_SEARCH_BACKEND=postgres`

### Problem: search_codebase() returns no results
**Solution**: Enable fallback: `WAVERIDER_FALLBACK_ENABLED=true`

### Problem: Slow search performance
**Postgres**: Check pgvector HNSW index exists (`\d coco_snippets` in psql)
**Neo4j**: Check graph is populated (use `neo4j_status()` tool)

### Problem: BM25 results seem different between backends
**Analysis**: Use `WAVERIDER_SEARCH_HYBRID=false` to isolate vector search
**Note**: Postgres pg_bm25 scoring may differ from tsvector fallback

## Configuration Resolution Order

1. Environment variables (highest priority)
2. Defaults (lowest priority)

Defaults:
- WAVERIDER_SEARCH_BACKEND = "postgres"
- WAVERIDER_SEARCH_HYBRID = "true"
- WAVERIDER_FALLBACK_ENABLED = "true"

## Code Integration

### Python API
```python
from waverider.config import get_config

config = get_config()

if config.is_postgres():
    # Use Postgres-specific logic
    pass
elif config.is_neo4j():
    # Use Neo4j-specific logic
    pass

if config.hybrid_search:
    # RRF fusion enabled
    pass
```

### MCP Server
```python
from waverider.config import get_config

@mcp.tool()
def my_tool():
    config = get_config()
    if config.is_postgres():
        return "Using Postgres backend"
```
