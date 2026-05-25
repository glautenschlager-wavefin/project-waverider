"""
PostgreSQL + pgvector + pg_bm25 (ParadeDB) database management for Waverider.
"""

import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

log = logging.getLogger(__name__)

_DEFAULT_DSN = "postgresql://waverider:changeme@localhost:5432/waverider"

# Regex for splitting camelCase / PascalCase identifiers at case boundaries.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def tokenize_code_identifiers(text: str) -> str:
    """Expand code identifiers so FTS5 can match sub-tokens.

    Given raw code text, this function finds identifiers that use
    ``snake_case``, ``camelCase``, ``PascalCase``, or ``dot.paths`` and
    appends their sub-tokens to the end of the text.  The original text is
    preserved verbatim so exact-match queries still work.

    Examples:
        ``DatabaseManager`` → appends ``database manager``
        ``add_embedding``   → appends ``add embedding``
        ``waverider.database`` → appends ``waverider database``
    """
    # Match word-like tokens (identifiers): letters, digits, underscores, dots
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", text)
    extra_tokens: list[str] = []

    seen: set[str] = set()
    for ident in identifiers:
        low = ident.lower()
        if low in seen or len(ident) < 3:
            continue
        seen.add(low)

        parts: list[str] = []

        # Split on dots first (e.g. waverider.database)
        for segment in ident.split("."):
            # Split on underscores (snake_case)
            for sub in segment.split("_"):
                # Split on camelCase boundaries
                camel_parts = _CAMEL_BOUNDARY.split(sub)
                parts.extend(p.lower() for p in camel_parts if p)

        # Only add if splitting produced multiple tokens
        if len(parts) > 1:
            extra_tokens.extend(parts)

    if not extra_tokens:
        return text

    return text + "\n" + " ".join(extra_tokens)


_SCHEMA_SQL = """\
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS codebase_metadata (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        UNIQUE NOT NULL,
    path        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    language    TEXT        NOT NULL DEFAULT 'mixed',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_files (
    id            BIGSERIAL PRIMARY KEY,
    codebase_id   BIGINT      NOT NULL REFERENCES codebase_metadata(id) ON DELETE CASCADE,
    file_path     TEXT        NOT NULL,
    relative_path TEXT        NOT NULL,
    content_hash  TEXT        NOT NULL DEFAULT '',
    file_size     INT         NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_files_codebase_path UNIQUE(codebase_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_source_files_codebase ON source_files(codebase_id);

CREATE TABLE IF NOT EXISTS code_snippets (
    id           BIGSERIAL PRIMARY KEY,
    file_id      BIGINT      NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    snippet_type TEXT        NOT NULL,
    name         TEXT        NOT NULL DEFAULT '',
    start_line   INT         NOT NULL DEFAULT 0,
    end_line     INT         NOT NULL DEFAULT 0,
    content      TEXT        NOT NULL,
    language     TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_code_snippets_file ON code_snippets(file_id);

CREATE TABLE IF NOT EXISTS embeddings (
    id               BIGSERIAL PRIMARY KEY,
    snippet_id       BIGINT      NOT NULL REFERENCES code_snippets(id) ON DELETE CASCADE,
    model            TEXT        NOT NULL,
    embedding_vector vector(768),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_embeddings_snippet_model UNIQUE(snippet_id, model)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_snippet ON embeddings(snippet_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw    ON embeddings
    USING hnsw(embedding_vector vector_cosine_ops);

CREATE TABLE IF NOT EXISTS index_runs (
    id                   BIGSERIAL PRIMARY KEY,
    codebase_id          BIGINT      NOT NULL REFERENCES codebase_metadata(id),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ,
    status               TEXT        NOT NULL DEFAULT 'running',
    files_added          INT         NOT NULL DEFAULT 0,
    files_updated        INT         NOT NULL DEFAULT 0,
    files_deleted        INT         NOT NULL DEFAULT 0,
    embeddings_generated INT         NOT NULL DEFAULT 0,
    error_message        TEXT
);
"""

# pg_bm25 (ParadeDB) BM25 index over code_snippets.
# ParadeDB is a Postgres distribution that includes pg_bm25 for BM25 search.
# If pg_bm25 is not available, we fall back to tsvector + GIN for keyword search.
_BM25_INDEX_SQL = """\
CREATE INDEX IF NOT EXISTS code_snippets_bm25 ON code_snippets
USING bm25(id, name, content, language)
WITH (
    key_field   = 'id',
    text_fields = '{"name": {"tokenizer": {"type": "code"}}, "content": {"tokenizer": {"type": "code"}}, "language": {"tokenizer": {"type": "keyword"}}}'
);
"""

# Fallback: Postgres native tsvector + GIN index for keyword search
# Used when pg_bm25 is not available
_TSVECTOR_INDEX_SQL = """\
ALTER TABLE code_snippets ADD COLUMN IF NOT EXISTS content_tsvector tsvector GENERATED ALWAYS AS (to_tsvector('english', name || ' ' || content)) STORED;
CREATE INDEX IF NOT EXISTS idx_code_snippets_tsvector ON code_snippets USING GIN(content_tsvector);
"""


class DatabaseManager:
    """Manages PostgreSQL database with pgvector and pg_bm25 (ParadeDB)."""

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn or os.environ.get("DATABASE_URL", _DEFAULT_DSN)
        self._pool: Optional[ConnectionPool] = None  # type: ignore[type-arg]

    def _get_pool(self) -> ConnectionPool:  # type: ignore[type-arg]
        if self._pool is None:
            self._pool = ConnectionPool(
                self._dsn,
                min_size=1,
                max_size=10,
                configure=self._configure_conn,
                open=True,
            )
        return self._pool

    @staticmethod
    def _configure_conn(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
        conn.row_factory = dict_row
        register_vector(conn)

    def _conn(self):  # type: ignore[no-untyped-def]
        return self._get_pool().connection()

    def init_schema(self) -> None:
        with self._conn() as conn:
            # Execute base schema (may contain multiple statements)
            for stmt in _SCHEMA_SQL.split(';'):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            conn.commit()
            
            # Try pg_bm25 first (ParadeDB feature), fall back to tsvector
            try:
                for stmt in _BM25_INDEX_SQL.split(';'):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
                conn.commit()
                log.info("Using pg_bm25 for BM25 search")
            except Exception as e:
                conn.rollback()
                log.warning("pg_bm25 not available, using tsvector fallback: %s", e)
                try:
                    for stmt in _TSVECTOR_INDEX_SQL.split(';'):
                        stmt = stmt.strip()
                        if stmt:
                            conn.execute(stmt)
                    conn.commit()
                    log.info("Using tsvector + GIN for keyword search")
                except Exception as e2:
                    conn.rollback()
                    log.warning("tsvector index creation also failed: %s", e2)

    def add_codebase(
        self, name: str, path: str, description: str = "", language: str = "mixed"
    ) -> int:
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO codebase_metadata (name, path, description, language, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    path        = EXCLUDED.path,
                    description = EXCLUDED.description,
                    language    = EXCLUDED.language,
                    updated_at  = NOW()
                RETURNING id
                """,
                (name, path, description, language),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Failed to upsert codebase: {name}")
            return int(row["id"])

    def add_source_file(
        self, codebase_id: int, file_path: str, relative_path: str, content_hash: str
    ) -> int:
        file_size = Path(file_path).stat().st_size if Path(file_path).exists() else 0
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO source_files
                    (codebase_id, file_path, relative_path, content_hash, file_size, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (codebase_id, file_path) DO UPDATE SET
                    relative_path = EXCLUDED.relative_path,
                    content_hash  = EXCLUDED.content_hash,
                    file_size     = EXCLUDED.file_size,
                    updated_at    = NOW()
                RETURNING id
                """,
                (codebase_id, file_path, relative_path, content_hash, file_size),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Failed to upsert source file: {file_path}")
            return int(row["id"])

    def add_code_snippet(
        self,
        file_id: int,
        snippet_type: str,
        content: str,
        name: str = "",
        start_line: int = 0,
        end_line: int = 0,
        language: str = "python",
    ) -> int:
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO code_snippets
                    (file_id, snippet_type, name, start_line, end_line, content, language)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (file_id, snippet_type, name, start_line, end_line, content, language),
            ).fetchone()
            if row is None:
                raise RuntimeError("Failed to insert code snippet")
            return int(row["id"])

    def add_embedding(
        self, snippet_id: int, embedding: List[float], model: str = "nomic-embed-text"
    ) -> int:
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO embeddings (snippet_id, model, embedding_vector)
                VALUES (%s, %s, %s::vector)
                ON CONFLICT (snippet_id, model) DO UPDATE SET
                    embedding_vector = EXCLUDED.embedding_vector
                RETURNING id
                """,
                (snippet_id, model, embedding),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Failed to upsert embedding for snippet {snippet_id}")
            return int(row["id"])

    def search_bm25(
        self,
        query: str,
        codebase_id: int,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Keyword search for code snippets.
        
        Tries pg_bm25 (ParadeDB) first for true BM25 ranking.
        Falls back to tsvector + GIN if pg_bm25 is not available.
        """
        safe_query = re.sub(r"[^\w\s]", " ", query).strip()
        if not safe_query:
            return []
        tokens = list(dict.fromkeys(re.findall(r"\w+", safe_query)))
        if not tokens:
            return []

        with self._conn() as conn:
            # Try pg_bm25 first (ParadeDB)
            try:
                bm25_query = " OR ".join(f"name:{t} OR content:{t}" for t in tokens)
                rows = conn.execute(
                    """
                    SELECT
                        cs.id,
                        cs.name,
                        cs.snippet_type,
                        cs.content,
                        sf.relative_path       AS file_path,
                        cs.start_line,
                        cs.end_line,
                        cs.language,
                        paradedb.score(cs.id)  AS bm25_score
                    FROM code_snippets cs
                    JOIN source_files sf ON cs.file_id = sf.id
                    WHERE cs.id @@@ paradedb.parse(%s)
                      AND sf.codebase_id = %s
                    ORDER BY bm25_score DESC
                    LIMIT %s
                    """,
                    (bm25_query, codebase_id, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                conn.rollback()
                log.debug("pg_bm25 search failed, falling back to tsvector: %s", e)

            # Fallback to tsvector + GIN
            try:
                # Build tsvector query from tokens
                ts_query = " | ".join(f"'{t}'" for t in tokens)
                rows = conn.execute(
                    """
                    SELECT
                        cs.id,
                        cs.name,
                        cs.snippet_type,
                        cs.content,
                        sf.relative_path       AS file_path,
                        cs.start_line,
                        cs.end_line,
                        cs.language,
                        ts_rank(content_tsvector, plainto_tsquery(%s)) AS bm25_score
                    FROM code_snippets cs
                    JOIN source_files sf ON cs.file_id = sf.id
                    WHERE content_tsvector @@ plainto_tsquery(%s)
                      AND sf.codebase_id = %s
                    ORDER BY bm25_score DESC
                    LIMIT %s
                    """,
                    (safe_query, safe_query, codebase_id, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                log.warning("Both pg_bm25 and tsvector searches failed: %s", e)
                return []

    def search_embeddings(
        self,
        query_embedding: List[float],
        codebase_id: int,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Cosine similarity search via pgvector HNSW index."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    cs.id,
                    cs.name,
                    cs.snippet_type,
                    cs.content,
                    sf.relative_path                                   AS file_path,
                    cs.start_line,
                    cs.end_line,
                    cs.language,
                    ROUND((1 - (e.embedding_vector <=> %s::vector))::numeric, 4) AS similarity
                FROM embeddings e
                JOIN code_snippets cs ON e.snippet_id = cs.id
                JOIN source_files sf  ON cs.file_id   = sf.id
                WHERE sf.codebase_id = %s
                  AND (1 - (e.embedding_vector <=> %s::vector)) >= %s
                ORDER BY e.embedding_vector <=> %s::vector
                LIMIT %s
                """,
                (query_embedding, codebase_id, query_embedding, threshold, query_embedding, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_codebase(self, name: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM codebase_metadata WHERE name = %s", (name,)
            ).fetchone()
        return dict(row) if row else None

    def list_codebases(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM codebase_metadata ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def reset_codebase_contents(self, codebase_id: int) -> None:
        """Delete all source files (and cascaded snippets/embeddings) for a codebase."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM source_files WHERE codebase_id = %s", (codebase_id,)
            )

    def delete_file_contents(self, file_id: int) -> None:
        """Delete all snippets (and cascaded embeddings) for a file.

        The source_files row is kept so that add_source_file can UPSERT it.
        """
        with self._conn() as conn:
            conn.execute("DELETE FROM code_snippets WHERE file_id = %s", (file_id,))

    def delete_source_file(self, file_id: int) -> None:
        """Delete a source file row and cascade to its snippets and embeddings."""
        with self._conn() as conn:
            conn.execute("DELETE FROM source_files WHERE id = %s", (file_id,))

    def get_file_hashes(self, codebase_id: int) -> Dict[str, Tuple[int, str]]:
        """Return {relative_path: (file_id, content_hash)} for all indexed files."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, relative_path, content_hash
                FROM source_files
                WHERE codebase_id = %s
                """,
                (codebase_id,),
            ).fetchall()
        return {r["relative_path"]: (r["id"], r["content_hash"]) for r in rows}

    def get_statistics(self, codebase_id: int) -> Dict[str, Any]:
        with self._conn() as conn:
            total_files = conn.execute(
                "SELECT COUNT(*) AS c FROM source_files WHERE codebase_id = %s",
                (codebase_id,),
            ).fetchone()["c"]
            total_snippets = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM code_snippets cs
                JOIN source_files sf ON cs.file_id = sf.id
                WHERE sf.codebase_id = %s
                """,
                (codebase_id,),
            ).fetchone()["c"]
            total_embeddings = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM embeddings e
                JOIN code_snippets cs ON e.snippet_id = cs.id
                JOIN source_files sf  ON cs.file_id   = sf.id
                WHERE sf.codebase_id = %s
                """,
                (codebase_id,),
            ).fetchone()["c"]
        return {
            "total_files": total_files,
            "total_snippets": total_snippets,
            "total_embeddings": total_embeddings,
        }

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    # ------------------------------------------------------------------
    # CocoIndex table search
    # These methods query the "coco_snippets" table managed by CocoIndex
    # (see src/waverider/cocoindex_app.py).  They are the primary search
    # path used by the MCP server once Phase 2 indexing is active.
    # ------------------------------------------------------------------

    _COCO_TABLE = "coco_snippets"

    def search_coco_embeddings(
        self,
        query_embedding: List[float],
        codebase_name: str,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Cosine similarity search over the CocoIndex-managed snippet table."""
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    id,
                    name,
                    snippet_type,
                    content,
                    file_path,
                    start_line,
                    end_line,
                    language,
                    ROUND((1 - (embedding <=> %s::vector))::numeric, 4) AS similarity
                FROM {self._COCO_TABLE}
                WHERE codebase_name = %s
                  AND (1 - (embedding <=> %s::vector)) >= %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (query_embedding, codebase_name, query_embedding, threshold, query_embedding, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_coco_bm25(
        self,
        query: str,
        codebase_name: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Keyword search over the CocoIndex-managed snippet table using tsvector.

        Falls back gracefully if pg_bm25 is not available.
        """
        safe_query = re.sub(r"[^\w\s]", " ", query).strip()
        if not safe_query:
            return []

        with self._conn() as conn:
            try:
                rows = conn.execute(
                    f"""
                    SELECT
                        id,
                        name,
                        snippet_type,
                        content,
                        file_path,
                        start_line,
                        end_line,
                        language,
                        ts_rank(to_tsvector('english', content), plainto_tsquery(%s)) AS bm25_score
                    FROM {self._COCO_TABLE}
                    WHERE to_tsvector('english', content) @@ plainto_tsquery(%s)
                      AND codebase_name = %s
                    ORDER BY bm25_score DESC
                    LIMIT %s
                    """,
                    (safe_query, safe_query, codebase_name, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception as exc:
                conn.rollback()
                log.warning("coco_bm25 search failed: %s", exc)
                return []

    def coco_table_exists(self) -> bool:
        """Return True if the CocoIndex snippet table has been created."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT to_regclass(%s) AS t",
                (self._COCO_TABLE,),
            ).fetchone()
        return row is not None and row["t"] is not None

    def search_symbols_by_name(
        self,
        query: str,
        codebase_id: int,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search for files, functions, and classes by name matching.
        
        This is a symbol-focused search that prioritizes exact/prefix name matches
        over full-text content search. Useful for IDE-like lookups.
        
        Returns snippets with matched_type indicating whether it matched a file path,
        function name, or class name.
        """
        # Escape SQL LIKE wildcards: % -> %%, _ -> \_
        safe_query = f"%{query.lower().replace('%', '%%').replace('_', r'\_')}%"
        
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    cs.id,
                    cs.name,
                    cs.snippet_type,
                    cs.content,
                    sf.relative_path        AS file_path,
                    cs.start_line,
                    cs.end_line,
                    cs.language,
                    CASE
                        WHEN LOWER(sf.relative_path) LIKE %s ESCAPE '\' THEN 'file'
                        WHEN cs.snippet_type = 'function' AND LOWER(cs.name) LIKE %s ESCAPE '\' THEN 'function'
                        WHEN cs.snippet_type = 'class' AND LOWER(cs.name) LIKE %s ESCAPE '\' THEN 'class'
                        ELSE 'content'
                    END AS match_type,
                    CASE
                        WHEN LOWER(sf.relative_path) LIKE %s ESCAPE '\' THEN 1
                        WHEN LOWER(cs.name) LIKE %s ESCAPE '\' THEN 2
                        ELSE 3
                    END AS match_priority
                FROM code_snippets cs
                JOIN source_files sf ON cs.file_id = sf.id
                WHERE sf.codebase_id = %s
                  AND (
                    LOWER(sf.relative_path) LIKE %s ESCAPE '\'
                    OR LOWER(cs.name) LIKE %s ESCAPE '\'
                  )
                ORDER BY match_priority ASC, cs.name ASC
                LIMIT %s
                """,
                (safe_query, safe_query, safe_query, safe_query, safe_query, codebase_id, safe_query, safe_query, limit),
            ).fetchall()
        return [dict(r) for r in rows]
