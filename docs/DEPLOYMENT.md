# Waverider Deployment Baseline

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   Host / VM                          │
│                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐       │
│  │ Waverider│   │ ParadeDB │   │  Neo4j 5 │       │
│  │  (MCP)   │──▶│ (PG+BM25 │   │ (graph)  │       │
│  │ :8000    │   │  +vector) │   │ :7687    │       │
│  └──────────┘   │ :5432    │   └──────────┘       │
│       │         └──────────┘         ▲             │
│       │              ▲               │             │
│       └──────────────┴───────────────┘             │
│                                                     │
│  ┌──────────┐                                       │
│  │  Ollama  │  (host-native on macOS for Metal)    │
│  │ :11434   │  (or GPU container on Linux)         │
│  └──────────┘                                       │
└─────────────────────────────────────────────────────┘
```

Waverider is a single-node deployment comprising:

- **Waverider MCP server** — Python process exposing SSE on port 8000
- **ParadeDB** — Postgres 17 with pg_bm25 + pgvector extensions (single cluster)
- **Neo4j 5** — Knowledge graph for structural relationships (optional)
- **Ollama** — Embedding model server (host-native on macOS, containerized on Linux)

## Single Postgres Cluster (ParadeDB)

All persistent state lives in one Postgres database (`waverider` on ParadeDB):

| Table | Purpose |
|-------|---------|
| `coco_snippets` | CocoIndex-managed code snippets with vector embeddings + tsvector |
| `coco_internal_*` | CocoIndex operational state (change tracking, flow metadata) |
| `codebases` | Codebase registry (name, description, path, indexed_at) |

### Extensions enabled
- `pgvector` — HNSW index for cosine similarity search
- `pg_bm25` — BM25 full-text search (Tantivy-backed)

## Connection Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://waverider:changeme@paradedb:5432/waverider` | Main app connection |
| `COCOINDEX_DB_URL` | Same as DATABASE_URL | CocoIndex internal state |
| `NEO4J_URI` | `bolt://neo4j:7687` | Neo4j Bolt protocol |
| `NEO4J_PASSWORD` | `changeme` | Neo4j auth |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama API |
| `OLLAMA_MODEL` | `nomic-embed-text` | Embedding model |

### Connection Pooling

For single-user / small-team use (current state), application-level pooling via `asyncpg` pool is sufficient:

```python
# CocoIndex manages its own connection pool internally.
# For direct queries (search, explore_graph), use:
pool = asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
```

For multi-tenant or high-concurrency deployment, add PgBouncer as a sidecar:

```yaml
# docker-compose.override.yml
pgbouncer:
  image: edoburu/pgbouncer:latest
  environment:
    DATABASE_URL: postgresql://waverider:${POSTGRES_PASSWORD}@paradedb:5432/waverider
    POOL_MODE: transaction
    MAX_CLIENT_CONN: 100
    DEFAULT_POOL_SIZE: 20
  ports:
    - "6432:6432"
  depends_on:
    paradedb:
      condition: service_healthy
```

## Backup & Restore

### Automated backup (pg_dump)

```bash
# Full logical backup (includes all tables, indexes, extensions)
docker compose exec paradedb pg_dump -U waverider -d waverider -Fc -f /tmp/waverider_backup.dump

# Copy to host
docker cp waverider-paradedb:/tmp/waverider_backup.dump ./backups/waverider_$(date +%Y%m%d).dump
```

### Restore

```bash
# Restore to a fresh database
docker cp ./backups/waverider_20250101.dump waverider-paradedb:/tmp/restore.dump
docker compose exec paradedb pg_restore -U waverider -d waverider --clean --if-exists /tmp/restore.dump
```

### Re-indexing from source (alternative to backup)

Since all data is derived from source code, a full re-index is always an option:

```bash
# Nuclear option: drop and rebuild everything
docker compose down -v          # removes volumes
docker compose up -d            # fresh ParadeDB + Neo4j
make index                      # re-index waverider
make index-all                  # re-index all Wave repos
```

### Neo4j backup

```bash
docker compose exec neo4j neo4j-admin database dump neo4j --to-path=/tmp/
docker cp waverider-neo4j:/tmp/neo4j.dump ./backups/
```

## Health Checks

All services have Docker health checks configured in `docker-compose.yml`:

| Service | Check | Interval | Retries |
|---------|-------|----------|---------|
| ParadeDB | `pg_isready -U waverider -d waverider` | 10s | 5 |
| Neo4j | `cypher-shell RETURN 1` | 10s | 10 |
| Waverider | HTTP GET `:8000/health` (when SSE transport) | — | — |

### Manual health verification

```bash
# All services running?
make docker-ps

# Database accessible?
make db-shell
# \dt   -- list tables
# \q    -- quit

# Database stats
make db-status

# Neo4j accessible?
docker compose exec neo4j cypher-shell -u neo4j -p changeme "MATCH (n) RETURN count(n);"
```

## Observability

### Logging

- Waverider logs to stdout (structured Python logging)
- ParadeDB logs to Docker (standard Postgres log format)
- Neo4j logs to `/logs` volume

View all logs:
```bash
make docker-logs           # tail all services
docker compose logs -f waverider   # just MCP server
```

### Metrics (future)

For production deployment, consider:
- **pg_stat_statements** — query performance (already available in ParadeDB)
- **Prometheus postgres_exporter** — connection pool utilization, query latency
- **Waverider `/metrics` endpoint** — search latency p50/p95, cache hit rates

## Resource Requirements

### Minimum (development / single-user)

| Resource | Requirement |
|----------|-------------|
| RAM | 4 GB (ParadeDB ~1GB, Neo4j ~1GB, Waverider ~512MB, Ollama ~1.5GB) |
| Disk | 2 GB base + ~500MB per indexed codebase |
| CPU | 2 cores |

### Recommended (team use, 5-10 engineers)

| Resource | Requirement |
|----------|-------------|
| RAM | 8 GB |
| Disk | 20 GB SSD |
| CPU | 4 cores |

## Upgrade Procedure

1. Pull latest images: `docker compose pull`
2. Rebuild Waverider: `make docker-build`
3. Rolling restart: `docker compose up -d`
4. Verify health: `make docker-ps && make db-status`

ParadeDB and Neo4j handle schema migrations automatically on startup. CocoIndex manages its own internal schema versioning.

## Security Notes

- Change default passwords via `.env` file (never commit `.env`)
- ParadeDB is not exposed externally by default (bind to `127.0.0.1:5432`)
- Neo4j browser (7474) should be firewalled in production
- Waverider MCP endpoint (8000) uses SSE — add TLS termination (nginx/caddy) for remote access
