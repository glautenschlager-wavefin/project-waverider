"""
SQLite database management for Waverider.
"""

import re
import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Tuple, Dict
from datetime import datetime
import json
import logging

import numpy as np

log = logging.getLogger(__name__)

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


class DatabaseManager:
    """Manages SQLite database for storing codebases, embeddings, and indices."""

    def __init__(self, db_path: str = "data/waverider.db"):
        """Initialize database manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._faiss_cache: Dict[int, Tuple[Any, np.ndarray]] = {}  # codebase_id -> (index, id_map)

    def connect(self) -> sqlite3.Connection:
        """Create and return database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """Initialize database schema with all required tables."""
        conn = self.connect()
        cursor = conn.cursor()

        # Codebase metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS codebase_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                description TEXT,
                language TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Source files table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codebase_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_hash TEXT,
                file_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (codebase_id) REFERENCES codebase_metadata(id),
                UNIQUE(codebase_id, file_path)
            )
        """)

        # Code snippets table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_snippets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                snippet_type TEXT NOT NULL,
                name TEXT,
                start_line INTEGER,
                end_line INTEGER,
                content TEXT NOT NULL,
                language TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES source_files(id)
            )
        """)

        # Embeddings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snippet_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                embedding_vector TEXT NOT NULL,
                embedding_dimensions INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (snippet_id) REFERENCES code_snippets(id),
                UNIQUE(snippet_id, model)
            )
        """)

        # Indices table (tracks index build history)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_name TEXT UNIQUE NOT NULL,
                codebase_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                total_snippets INTEGER,
                total_embeddings INTEGER,
                status TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                metadata TEXT,
                FOREIGN KEY (codebase_id) REFERENCES codebase_metadata(id)
            )
        """)

        # Create indices for faster queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_codebase ON source_files(codebase_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snippets_file ON code_snippets(file_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_snippet ON embeddings(snippet_id)"
        )

        # FTS5 full-text index over code snippets for BM25 keyword search.
        # Uses a content-less (external content) table so snippet text is not
        # duplicated — FTS5 reads through to code_snippets via rowid.
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS code_snippets_fts USING fts5(
                name,
                content,
                file_path,
                content=code_snippets,
                content_rowid=id,
                tokenize='unicode61'
            )
        """)

        conn.commit()
        conn.close()

    def add_codebase(
        self, name: str, path: str, description: str = "", language: str = "mixed"
    ) -> int:
        """Add or update a codebase record and return its ID.

        Args:
            name: Unique codebase identifier
            path: Full path to codebase
            description: Optional description
            language: Programming language(s)

        Returns:
            Codebase ID
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO codebase_metadata (name, path, description, language, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    path = excluded.path,
                    description = excluded.description,
                    language = excluded.language,
                    updated_at = CURRENT_TIMESTAMP
            """,
                (name, path, description, language),
            )

            cursor.execute("SELECT id FROM codebase_metadata WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Failed to create or retrieve codebase: {name}")

            codebase_id = int(row["id"])
            conn.commit()
            return codebase_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add_source_file(
        self, codebase_id: int, file_path: str, relative_path: str, content_hash: str
    ) -> int:
        """Add or update a source file and return its ID.

        Args:
            codebase_id: ID of the parent codebase
            file_path: Full file path
            relative_path: Path relative to codebase root
            content_hash: Hash of file content

        Returns:
            File ID
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()

            file_size = Path(file_path).stat().st_size if Path(file_path).exists() else 0

            cursor.execute(
                """
                INSERT INTO source_files (
                    codebase_id, file_path, relative_path, content_hash, file_size, updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(codebase_id, file_path) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    content_hash = excluded.content_hash,
                    file_size = excluded.file_size,
                    updated_at = CURRENT_TIMESTAMP
            """,
                (codebase_id, file_path, relative_path, content_hash, file_size),
            )

            cursor.execute(
                """
                SELECT id
                FROM source_files
                WHERE codebase_id = ? AND file_path = ?
            """,
                (codebase_id, file_path),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Failed to create or retrieve source file: {file_path}")

            file_id = int(row["id"])
            conn.commit()
            return file_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
        """Add a code snippet.

        Args:
            file_id: ID of parent source file
            snippet_type: Type of snippet (function, class, import, etc.)
            content: The actual code content
            name: Optional name/identifier
            start_line: Starting line number
            end_line: Ending line number
            language: Programming language

        Returns:
            Snippet ID
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO code_snippets (file_id, snippet_type, name, start_line, end_line, content, language)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (file_id, snippet_type, name, start_line, end_line, content, language),
            )

            snippet_id = cursor.lastrowid
            conn.commit()
            return snippet_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # FTS5 full-text index helpers
    # ------------------------------------------------------------------

    def add_to_fts(
        self,
        snippet_id: int,
        name: str,
        content: str,
        file_path: str,
    ) -> None:
        """Insert a row into the FTS5 index with code-aware tokenization.

        Args:
            snippet_id: The rowid (code_snippets.id) to associate.
            name: Snippet name (function/class name).
            content: Raw code content.
            file_path: Relative file path.
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO code_snippets_fts(rowid, name, content, file_path)
                VALUES (?, ?, ?, ?)
                """,
                (
                    snippet_id,
                    tokenize_code_identifiers(name),
                    tokenize_code_identifiers(content),
                    tokenize_code_identifiers(file_path),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_from_fts(self, snippet_id: int) -> None:
        """Remove a row from the FTS5 index by rowid."""
        conn = self.connect()
        try:
            cursor = conn.cursor()
            # For external-content FTS5 tables, deletions use the special
            # 'delete' command with the original column values.  Since we
            # can't easily reconstruct the tokenized text, we rebuild the
            # FTS index for this snippet's file in the caller.  As a simpler
            # approach, we use the INSERT with delete command.
            cursor.execute(
                "INSERT INTO code_snippets_fts(code_snippets_fts, rowid, name, content, file_path) "
                "VALUES('delete', ?, '', '', '')",
                (snippet_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def search_bm25(
        self,
        query: str,
        codebase_id: int,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """BM25 keyword search over the FTS5 index.

        Args:
            query: Search terms (natural language or identifiers).
            codebase_id: Restrict results to this codebase.
            limit: Maximum results to return.

        Returns:
            List of result dicts with keys: id, name, snippet_type, content,
            file_path, start_line, end_line, language, bm25_score.
            Ordered by BM25 relevance (lower bm25 values = more relevant in
            SQLite; we negate for a higher-is-better score).
        """
        # Expand the query with code-aware sub-tokens so that searching for
        # "database" can also match "DatabaseManager" (whose expanded FTS
        # content includes the sub-token).
        expanded_query = tokenize_code_identifiers(query)
        # Convert to FTS5 query: quote each token to avoid syntax issues, then
        # OR them together for broad recall.
        tokens = re.findall(r"\w+", expanded_query)
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in tokens)

        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    cs.id,
                    cs.name,
                    cs.snippet_type,
                    cs.content,
                    sf.relative_path AS file_path,
                    cs.start_line,
                    cs.end_line,
                    cs.language,
                    fts.rank AS bm25_rank
                FROM code_snippets_fts fts
                JOIN code_snippets cs ON cs.id = fts.rowid
                JOIN source_files sf ON cs.file_id = sf.id
                WHERE code_snippets_fts MATCH ?
                  AND sf.codebase_id = ?
                ORDER BY fts.rank
                LIMIT ?
                """,
                (fts_query, codebase_id, limit),
            )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                # SQLite FTS5 rank is negative (more negative = better).
                # Negate so higher = more relevant.
                d["bm25_score"] = round(-d.pop("bm25_rank"), 4)
                results.append(d)
            return results
        finally:
            conn.close()

    def rebuild_fts_index(self, codebase_id: int) -> int:
        """Rebuild FTS5 index for an entire codebase from existing code_snippets.

        Useful as a one-time backfill or after schema changes.

        Returns:
            Number of rows inserted into FTS.
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT cs.id, cs.name, cs.content, sf.relative_path
                FROM code_snippets cs
                JOIN source_files sf ON cs.file_id = sf.id
                WHERE sf.codebase_id = ?
                """,
                (codebase_id,),
            )
            rows = cursor.fetchall()
            count = 0
            for row in rows:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO code_snippets_fts(rowid, name, content, file_path)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        tokenize_code_identifiers(row["name"] or ""),
                        tokenize_code_identifiers(row["content"]),
                        tokenize_code_identifiers(row["relative_path"]),
                    ),
                )
                count += 1
            conn.commit()
            return count
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add_embedding(
        self, snippet_id: int, embedding: List[float], model: str = "text-embedding-3-small"
    ) -> int:
        """Store an embedding vector for a code snippet.

        Args:
            snippet_id: ID of the code snippet
            embedding: List of floats representing the embedding
            model: Model used to generate the embedding

        Returns:
            Embedding ID
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()

            # Store embedding as JSON string
            embedding_str = json.dumps(embedding)
            dimensions = len(embedding)

            cursor.execute(
                """
                INSERT INTO embeddings (snippet_id, model, embedding_vector, embedding_dimensions)
                VALUES (?, ?, ?, ?)
            """,
                (snippet_id, model, embedding_str, dimensions),
            )

            embedding_id = cursor.lastrowid
            conn.commit()
            return embedding_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_codebase(self, name: str) -> Optional[Dict[str, Any]]:
        """Get codebase by name."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM codebase_metadata WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()

        return dict(row) if row else None

    def list_codebases(self) -> List[Dict[str, Any]]:
        """List all tracked codebases."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM codebase_metadata ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def reset_codebase_contents(self, codebase_id: int) -> None:
        """Delete all indexed files, snippets, and embeddings for a codebase.

        Keeps the codebase_metadata record so index rebuilds are idempotent
        for a stable codebase name.

        Args:
            codebase_id: ID of the codebase to reset
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                DELETE FROM embeddings
                WHERE snippet_id IN (
                    SELECT cs.id
                    FROM code_snippets cs
                    JOIN source_files sf ON cs.file_id = sf.id
                    WHERE sf.codebase_id = ?
                )
            """,
                (codebase_id,),
            )

            cursor.execute(
                """
                DELETE FROM code_snippets
                WHERE file_id IN (
                    SELECT id
                    FROM source_files
                    WHERE codebase_id = ?
                )
            """,
                (codebase_id,),
            )

            cursor.execute(
                """
                DELETE FROM source_files
                WHERE codebase_id = ?
            """,
                (codebase_id,),
            )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Precomputed vector index (numpy, with optional FAISS acceleration)
    # ------------------------------------------------------------------

    def _vector_paths(self, codebase_id: int) -> Tuple[Path, Path]:
        """Return (matrix_path, ids_path) for the precomputed vector index."""
        base = self.db_path.parent / f"vectors_{codebase_id}"
        return base.with_suffix(".npy"), base.with_suffix(".ids.npy")

    def build_vector_index(self, codebase_id: int) -> int:
        """Build a precomputed L2-normalised embedding matrix for fast search.

        Returns the number of vectors indexed.
        """
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT e.snippet_id, e.embedding_vector, e.embedding_dimensions
            FROM embeddings e
            JOIN code_snippets cs ON e.snippet_id = cs.id
            JOIN source_files sf ON cs.file_id = sf.id
            WHERE sf.codebase_id = ?
              AND e.embedding_dimensions > 0
            ORDER BY e.snippet_id
            """,
            (codebase_id,),
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return 0

        # Determine the expected dimension (most common)
        expected_dim = rows[0]["embedding_dimensions"]

        # Filter to only rows with consistent dimensions
        valid = [(r["snippet_id"], json.loads(r["embedding_vector"]))
                 for r in rows if r["embedding_dimensions"] == expected_dim]

        if not valid:
            return 0

        ids = np.array([v[0] for v in valid], dtype=np.int64)
        vecs = np.array([v[1] for v in valid], dtype=np.float32)

        skipped = len(rows) - len(valid)
        if skipped:
            log.warning("Skipped %d embeddings with mismatched dimensions", skipped)

        # L2-normalise so dot-product == cosine similarity
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms

        mat_path, ids_path = self._vector_paths(codebase_id)
        np.save(str(mat_path), vecs)
        np.save(str(ids_path), ids)

        self._faiss_cache[codebase_id] = (vecs, ids)
        log.info("Vector index built: %d vectors, dim=%d", len(ids), vecs.shape[1])
        return len(ids)

    def _load_vectors(self, codebase_id: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Load precomputed vectors from cache or disk."""
        if codebase_id in self._faiss_cache:
            return self._faiss_cache[codebase_id]

        mat_path, ids_path = self._vector_paths(codebase_id)
        if not mat_path.exists() or not ids_path.exists():
            return None

        vecs = np.load(str(mat_path))
        ids = np.load(str(ids_path))
        self._faiss_cache[codebase_id] = (vecs, ids)
        return vecs, ids

    def _search_vectors(
        self,
        query_embedding: List[float],
        codebase_id: int,
        limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """Numpy-vectorized cosine similarity search. Returns None if no index."""
        loaded = self._load_vectors(codebase_id)
        if loaded is None:
            return None

        mat, id_map = loaded
        qvec = np.array(query_embedding, dtype=np.float32)
        qnorm = np.linalg.norm(qvec)
        if qnorm == 0:
            return []
        qvec = qvec / qnorm

        # Vectorized dot-product (cosine similarity on pre-normalised matrix)
        scores = mat @ qvec  # shape: (n,)

        k = min(limit, len(scores))
        top_k = np.argpartition(-scores, k)[:k]
        top_k = top_k[np.argsort(-scores[top_k])]

        snippet_ids = [int(id_map[i]) for i in top_k]
        if not snippet_ids:
            return []

        conn = self.connect()
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in snippet_ids)
        cursor.execute(
            f"""
            SELECT cs.id, cs.name, cs.snippet_type, cs.content,
                   sf.relative_path AS file_path,
                   cs.start_line, cs.end_line, cs.language
            FROM code_snippets cs
            JOIN source_files sf ON cs.file_id = sf.id
            WHERE cs.id IN ({placeholders})
            """,
            snippet_ids,
        )
        rows = {r["id"]: dict(r) for r in cursor.fetchall()}
        conn.close()

        results = []
        for idx_pos in top_k:
            sid = int(id_map[idx_pos])
            if sid in rows:
                row = rows[sid]
                row["similarity"] = round(float(scores[idx_pos]), 4)
                results.append(row)
        return results

    # ------------------------------------------------------------------
    # Embedding search (precomputed index with brute-force fallback)
    # ------------------------------------------------------------------

    def search_embeddings(
        self,
        query_embedding: List[float],
        codebase_id: int,
        limit: int = 10,
        threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Return the top-k snippets most similar to query_embedding (cosine similarity).

        Uses precomputed numpy vector index if available, otherwise falls back
        to a pure-Python brute-force computation.
        """
        fast_results = self._search_vectors(query_embedding, codebase_id, limit)
        if fast_results is not None:
            return fast_results

        return self._search_brute_force(query_embedding, codebase_id, limit, threshold)

    def _search_brute_force(
        self,
        query_embedding: List[float],
        codebase_id: int,
        limit: int,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        """Pure-Python brute-force cosine similarity search (fallback)."""
        import math

        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT cs.id, cs.name, cs.snippet_type, cs.content,
                   sf.relative_path AS file_path,
                   cs.start_line, cs.end_line, cs.language,
                   e.embedding_vector
            FROM code_snippets cs
            JOIN source_files sf ON cs.file_id = sf.id
            JOIN embeddings e ON cs.id = e.snippet_id
            WHERE sf.codebase_id = ?
            """,
            (codebase_id,),
        )
        rows = cursor.fetchall()
        conn.close()

        q = query_embedding
        q_norm = math.sqrt(sum(x * x for x in q))
        if q_norm == 0:
            return []

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            d = dict(row)
            vec = json.loads(d.pop("embedding_vector"))
            v_norm = math.sqrt(sum(x * x for x in vec))
            if v_norm == 0:
                continue
            similarity = sum(a * b for a, b in zip(q, vec)) / (q_norm * v_norm)
            if similarity >= threshold:
                d["similarity"] = round(similarity, 4)
                scored.append((similarity, d))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:limit]]

    # ------------------------------------------------------------------
    # Per-file deletion (for incremental re-indexing)
    # ------------------------------------------------------------------

    def delete_file_contents(self, file_id: int) -> None:
        """Delete all snippets and embeddings for a specific source file.

        The source_file row itself is kept so that add_source_file can UPSERT it.
        """
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM embeddings
                WHERE snippet_id IN (
                    SELECT id FROM code_snippets WHERE file_id = ?
                )
                """,
                (file_id,),
            )
            cursor.execute("DELETE FROM code_snippets WHERE file_id = ?", (file_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_source_file(self, file_id: int) -> None:
        """Delete a source file and all its snippets / embeddings."""
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM embeddings
                WHERE snippet_id IN (
                    SELECT id FROM code_snippets WHERE file_id = ?
                )
                """,
                (file_id,),
            )
            cursor.execute("DELETE FROM code_snippets WHERE file_id = ?", (file_id,))
            cursor.execute("DELETE FROM source_files WHERE id = ?", (file_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_file_hashes(self, codebase_id: int) -> Dict[str, Tuple[int, str]]:
        """Return {relative_path: (file_id, content_hash)} for all indexed files."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, relative_path, content_hash FROM source_files WHERE codebase_id = ?",
            (codebase_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        return {row["relative_path"]: (row["id"], row["content_hash"]) for row in rows}

    def get_statistics(self, codebase_id: int) -> Dict[str, Any]:
        """Get statistics for a codebase."""
        conn = self.connect()
        cursor = conn.cursor()

        # Total files
        cursor.execute("SELECT COUNT(*) FROM source_files WHERE codebase_id = ?", (codebase_id,))
        total_files = cursor.fetchone()[0]

        # Total snippets
        cursor.execute(
            """
            SELECT COUNT(*) FROM code_snippets cs
            JOIN source_files sf ON cs.file_id = sf.id
            WHERE sf.codebase_id = ?
        """,
            (codebase_id,),
        )
        total_snippets = cursor.fetchone()[0]

        # Total embeddings
        cursor.execute(
            """
            SELECT COUNT(*) FROM embeddings e
            JOIN code_snippets cs ON e.snippet_id = cs.id
            JOIN source_files sf ON cs.file_id = sf.id
            WHERE sf.codebase_id = ?
        """,
            (codebase_id,),
        )
        total_embeddings = cursor.fetchone()[0]

        conn.close()

        return {
            "total_files": total_files,
            "total_snippets": total_snippets,
            "total_embeddings": total_embeddings,
        }
