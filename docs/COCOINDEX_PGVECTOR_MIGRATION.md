# CocoIndex + pgVector Combined Migration Plan

For a centralized service for ~100 engineers and no current production deployment, the recommended path is a combined migration: implement CocoIndex as the incremental execution engine and pgVector/Postgres as the new system of record in the same program, but with phase gates. This avoids doing schema work twice (once for legacy indexer+pgVector, then again for CocoIndex) and gives a clean long-term architecture immediately.

## Steps

### Phase 0: Architecture decision and target contract

1. Define canonical target schema in Postgres for codebases, files, snippets/chunks, embeddings, and optional lexical index support. Include stable primary keys and per-file reconciliation keys. *Depends on no prior steps.*
2. Define search contract compatibility for MCP responses (path, line ranges, snippet text, score semantics), allowing internals to change while external behavior remains stable. *Depends on step 1.*

### Phase 1: Introduce Postgres/pgVector foundation

3. Add Postgres connectivity and migration mechanism (schema SQL or migration tool), including pgvector extension setup and essential indexes (btree + ivfflat/hnsw strategy chosen per scale). *Depends on step 2.*
4. Implement database access abstraction so query/index paths can switch between SQLite (legacy) and Postgres (new) during rollout verification. *Depends on step 3.*
5. Implement parity query primitives in Postgres for semantic search and metadata retrieval currently served by DatabaseManager methods. *Depends on step 4.*

### Phase 2: Implement CocoIndex indexing app against Postgres targets

6. Create CocoIndex app entrypoint with localfs source and file-level processing components keyed by relative path. *Depends on step 3.*
7. Reuse current tree-sitter extraction pipeline to emit equivalent snippet units and metadata (type/name/start/end/language). *Depends on step 6.*
8. Implement memoized process_file and target declaration logic so CocoIndex handles add/modify/delete reconciliation directly in Postgres tables. *Depends on step 7.*
9. Configure embedding context/change detection so model changes intentionally invalidate memoized results when needed. *Parallel with step 8.*

### Phase 3: Search path cutover to Postgres

10. Add Postgres-backed search implementations (hybrid lexical + vector as needed) in MCP server code paths, keeping tool APIs unchanged. *Depends on steps 5 and 8.*
11. Preserve or reimplement BM25-equivalent behavior using Postgres full-text search (or maintain lexical scoring approximation), with ranking fusion retained in application layer. *Depends on step 10.*
12. Add feature flag/config switch for search backend (`sqlite` vs `postgres`) to support side-by-side validation before full cutover. *Depends on step 10.*

### Phase 4: Operational command and Docker cutover

13. Replace `scripts/build_index.py` orchestration to invoke CocoIndex update against Postgres target by default. *Depends on step 8.*
14. Update `Makefile` and `scripts/index_wave_repos.sh` to remove forced full rebuilds and run incremental CocoIndex updates; add Postgres service/env wiring in docker-compose for shared deployment. *Depends on step 13.*
15. Define centralized service deployment baseline (single Postgres cluster, connection pooling, backup/restore, observability hooks). *Depends on step 14.*

### Phase 5: Validation, scale checks, and cleanup

16. Run side-by-side corpus validation on large repos (identity/accounting/next-wave): count parity, retrieval quality, and incremental latency after small file deltas. *Depends on steps 12 and 14.*
17. Perform team-scale readiness checks for ~100 engineers: concurrent query load test, index update contention, and p95/p99 latency targets. *Depends on step 16.*
18. Remove legacy SQLite indexing/search paths after acceptance criteria are met; keep one rollback window before hard removal. *Depends on step 17.*
19. Update docs and runbooks for onboarding, operations, incident handling, and schema evolution in Postgres. *Depends on step 18.*

## Relevant Files

- `src/waverider/database.py` — refactor toward backend abstraction and Postgres implementation
- `src/waverider/indexer.py` — extraction reuse points; eventually reduced after CocoIndex ownership
- `src/waverider/treesitter_parser.py` — snippet extraction contract to preserve
- `src/waverider/mcp_server.py` — search backend cutover and feature-flag support
- `scripts/build_index.py` — CocoIndex-driven update orchestration
- `scripts/index_wave_repos.sh` — multi-repo incremental update path
- `Makefile` — operational command updates for Postgres/CocoIndex
- `docker-compose.yml` — Postgres service, env vars, persistence, networking
- `tests/test_database.py` — backend parity and reconciliation tests
- `tests/test_indexer.py` — extraction parity tests

## Verification

1. Schema verification: run migrations in a clean environment and confirm pgvector extension/indexes created correctly.
2. Incremental correctness: add/modify/delete file scenarios across two consecutive runs with CocoIndex; verify expected row-level delta in Postgres.
3. Search parity: compare top-k results and provenance formatting between SQLite baseline and Postgres backend on representative queries.
4. Performance: benchmark incremental update time and embedding-call volume on small changes; benchmark query p50/p95/p99 under concurrent load.
5. Reliability: validate backup/restore and rollback procedures for Postgres before enabling as default backend.

## Decisions

- Recommended sequencing: combined migration (CocoIndex + pgVector) with phase-gated cutover, not separate serial migrations.
- Rationale: no production users yet, schema rewrites acceptable, and serial migration would duplicate integration work.
- Keep tree-sitter extraction contract stable to reduce search behavior drift during storage/runtime changes.

## Further Considerations

1. Neo4j is currently separate and full-rebuild oriented; keep it out of this migration and plan a dedicated incremental graph phase afterward.
2. Choose vector index strategy (HNSW vs IVF) based on expected corpus size and update frequency; revisit after initial load tests.
3. Define SLOs early (freshness lag, query latency, indexing throughput) so architecture decisions are measurable, not preference-driven.
